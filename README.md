# StopSlop

StopSlop checks AI requests and responses against privacy, security, and usage policies. It can redact sensitive data, block prohibited content, route requests to a local model, and control agent permissions through an HTTP gateway or Python SDK.

## Install and run

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), [just](https://just.systems/man/en/chapter_4.html), and [Ollama](https://ollama.com/download). From the repository root:

```sh
just setup
just demo-local
just top          # In another terminal: dashboard
```

Setup installs Python 3.14 and the workspace dependencies, pulls `qwen3:0.6b`, and creates `.env` for local Qwen chat and Laya CPU assessment. No hosted API keys are required. Re-running setup replaces `.env`; the first demo may take longer while Laya downloads and loads.

Use `just demo` to run with the provider configured in `.env`, or `just proxy` to start the HTTP gateway.

## Warning: Laya demo quality

Laya was not fine-tuned for this project due to time constraints, so its results may be less reliable than hosted Jev. The classifier and chat model can be freely swapped in `.env`; see the [configuration guide](https://frankoslaw.github.io/StopSlop/reference/configuration/).

The demo commands quietly skip failed turns and continue. Policy enforcement remains active, but demo completion does not verify classifier accuracy. Use `just demo-local --no-best-effort` for strict scenario checks.

## Documentation

- [Introduction](https://frankoslaw.github.io/StopSlop/start/introduction/): what StopSlop does and how it works.
- [Documentation](https://frankoslaw.github.io/StopSlop/): configuration, integrations, demos, and operations.
- [Policy reference](https://frankoslaw.github.io/StopSlop/reference/policy/): editing `policy.toml`, actions, thresholds, and budgets.
