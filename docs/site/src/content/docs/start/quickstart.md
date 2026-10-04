---
title: Quickstart
description: Install the workspace, configure a provider, and send your first protected request.
---

## Prerequisites

Install [uv](https://docs.astral.sh/uv/), [just](https://just.systems/man/en/chapter_4.html), and [Ollama](https://ollama.com/download). Run commands from the repository root.

## 1. Install and configure

```sh
just setup
```

Setup installs Python 3.14 if needed and all workspace packages with the Laya extra, starts local Ollama if necessary, and pulls `qwen3:0.6b`. It recreates `.env` from `.env.example` using the installed model reported by Ollama. Re-running setup replaces `.env`; policy and usage history remain intact.

The local defaults use Qwen chat and Laya CPU assessment. No hosted provider or Jev key is required. The first demo downloads and loads Laya weights. Ollama stays running; `just ollama-serve` restarts it if needed.

## 2. Verify the demo

```sh
just demo-local
```

The local scripted scenario checks expected allowed, filtered, and blocked behavior using invented examples. Its final block uses an explicit confidentiality marker; `--scenario nda` separately demonstrates semantic-only NDA blocking. Use a second terminal to inspect telemetry:

```sh
just top
```

For a single prompt:

```sh
uv run --no-sync --package stopslop-demo stopslop-demo "Explain how rain forms."
```

Omit the prompt for interactive chat. See [CLI reference](../../reference/cli/) for retry behavior and options.

## 3. Start the gateway

```sh
uv run --no-sync --package stopslop-proxy stopslop-proxy
```

The default listener is `http://127.0.0.1:8000`. A successful `/health` response confirms the listener, but does not prove that upstream credentials, semantic classification, or fallback routes work.

## 4. Send a text request

Install `openai` in your client environment. Use the gateway URL and a nonempty dummy key only for an unauthenticated loopback gateway:

```python
from openai import OpenAI

with OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="local-client",
    max_retries=0,
) as client:
    reply = client.chat.completions.create(
        model="qwen3:0.6b",
        messages=[{"role": "user", "content": "Explain how rain forms."}],
        max_tokens=96,
        stream=False,
    )
    print(reply.choices[0].message.content)
```

When `STOPSLOP_ACCESS_TOKENS` is configured, replace `local-client` with an assigned gateway token. The gateway supplies its own upstream credential. Automatic SDK retries are disabled so policy failures and quotas are handled explicitly by the application.

## Other configurations

`just demo` respects the provider in `.env`; `just demo-local` selects Ollama and Laya. Set all four main provider values together for hosted chat and approve its exact model in `policy.toml`. Jev is optional and needs its own key only when selected as the classifier.

The shipped daily cap is scoped to NVIDIA and does not cap Qwen chat. Add an overall or Qwen-specific budget if needed. For Qwen as the only chat model, set a Qwen budget's exhaustion action to `block`; budget fallback needs a different model.

Continue with [configuration](../../reference/configuration/) and [policy rules](../../policies/rules/).

## Daily commands and cleanup

```sh
just demo-local                  # Local Qwen chat and Laya policy assessment
just demo                        # Use the provider selected in .env
just top                         # Dashboard in another terminal
just proxy                       # HTTP gateway
just test                        # Test suite
uv run --no-sync --package stopslop-demo stopslop-demo  # Interactive chat
```

Demo, proxy, and dashboard use `STOPSLOP_STATE_FILE` from `.env`; keep it consistent. Stop all three before running `just clean` to reset recorded usage, incidents, and generated rules. Interactive chat does not retry provider calls; scripted scenarios retry only transient HTTP 502/503/504 failures.

For hosted chat, see [configuration](../../reference/configuration/). For policy editing, see the [policy reference](../../reference/policy/). To work on the docs themselves, see [documentation development](../../contributing/documentation/).
