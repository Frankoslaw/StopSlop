---
title: Quickstart
description: Install the workspace, configure a provider, and send your first protected request.
---

## Prerequisites

Use Python 3.14, [uv](https://docs.astral.sh/uv/), and a checkout of this repository. A hosted provider needs a credential; local Ollama needs an already installed model served on its OpenAI-compatible endpoint.

Commands below run from the repository root. Installation uses source packages until a release is published.

## 1. Install and configure

```sh
uv sync --all-packages
```

Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

On macOS or Linux, use `cp .env.example .env`.

The example selects NVIDIA chat and Jev semantic classification. Set `STOPSLOP_KEY` to the NVIDIA credential and `STOPSLOP_JEV_KEY` to the Jev credential. The shipped policy already approves the example NVIDIA model.

:::note[Defaults and the example file]
`Settings` defaults to the local Laya classifier, but `.env.example` explicitly selects Jev. A copied example file therefore needs a Jev key. To select Laya, install the optional dependency with `uv sync --all-packages --extra laya` and set `STOPSLOP_CLASSIFIER=laya`.
:::

## 2. Verify the demo

```sh
uv run --no-sync --package stopslop-demo stopslop-demo --scenario nda
```

The scripted scenario checks expected allowed, filtered, and blocked behavior using invented examples. It can make real provider and classifier calls. Use a second terminal to inspect telemetry:

```sh
uv run --no-sync --package stopslop-top stopslop-top
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
        model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        messages=[{"role": "user", "content": "Explain how rain forms."}],
        max_tokens=96,
        stream=False,
    )
    print(reply.choices[0].message.content)
```

When `STOPSLOP_ACCESS_TOKENS` is configured, replace `local-client` with an assigned gateway token. The gateway supplies its own upstream credential. Automatic SDK retries are disabled so policy failures and quotas are handled explicitly by the application.

## A fully local alternative

Install the Laya extra and configure these `.env` values, replacing the model with the exact name installed in Ollama:

```dotenv
STOPSLOP_PROVIDER=ollama
STOPSLOP_BASE_URL=http://127.0.0.1:11434/v1
STOPSLOP_MODEL=llama3.2
STOPSLOP_KEY=ollama
STOPSLOP_CLASSIFIER=laya
STOPSLOP_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
STOPSLOP_LOCAL_MODEL=llama3.2
STOPSLOP_LOCAL_KEY=local
```

Add `llama3.2` to `allowed_models` in `policy.toml`. Review budgets: the shipped daily cap is scoped to the NVIDIA model and will not cap Ollama usage. Add an overall or Ollama-specific budget if desired. StopSlop does not start Ollama or install chat models. Laya loads on CPU by default.

Continue with [configuration](../../reference/configuration/) and [policy rules](../../policies/rules/).
