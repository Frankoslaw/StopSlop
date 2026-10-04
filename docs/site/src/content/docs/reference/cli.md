---
title: CLI reference
description: Commands, package invocation, shared options, and exit behavior.
sidebar:
  order: 5
---

Commands are available in an installed environment. In a source checkout, run `uv sync --all-packages` first and use `uv run --no-sync --package PACKAGE COMMAND`. The root `justfile` supplies convenience recipes. `just setup` installs all packages with Laya, starts Ollama when needed, pulls Qwen, and recreates `.env` from the example using the installed model name. `just demo-local` runs the local scripted demo and `just top` opens the dashboard.

## stopslop-proxy

```sh
uv run --no-sync --package stopslop-proxy stopslop-proxy [OPTIONS]
```

All runtime settings expose flags from the [configuration reference](../configuration/). Gateway-specific flags:

| Flag | Default | Purpose |
| --- | --- | --- |
| `--host` | `127.0.0.1` | Listener address; non-loopback requires access tokens |
| `--port` | `8000` | Listener port |
| `--json-logs` | Off | Structured console logs |
| `--env-file` | `.env` | Selected dotenv file |

Configuration errors are reported through the argument parser before startup. Use `--help` for generated option help.

## stopslop-demo

```sh
uv run --no-sync --package stopslop-demo stopslop-demo [PROMPT] [OPTIONS]
```

Omit the prompt for interactive chat. `--scenario` runs scripted checks; passing it without a name selects `nda`.

| Flag | Default | Purpose |
| --- | --- | --- |
| `--scenario [NAME]` | None | Verified scripted scenario; available names shown by `--help` |
| `--max-tokens` | `96` | Maximum generated reply allowance |
| `--retries` | `2` | Scripted scenario retries for chat HTTP 502/503/504 only |
| `--color` | `auto` | `auto`, `always`, or `never` |

The demo also supports all shared runtime flags. Interactive and single-prompt chat never retry. Classifier failures and policy rejections are not retried. `--deterministic` skips semantic checks explicitly.

## stopslop-top

```sh
uv run --no-sync --package stopslop-top stopslop-top [OPTIONS]
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `--env-file` | `.env` | Resolve runtime state setting |
| `--state-file` | Configured state file | Read-only dashboard database |
| `--interval` | `0.5` | Refresh interval in seconds |
| `--once` | Off | Print one snapshot |

Redirecting output also prints a snapshot. See [monitoring](../../operations/monitoring/) for keyboard controls.

## stopslop-policy

```sh
uv run --no-sync --package stopslop stopslop-policy COMMAND [OPTIONS]
```

| Command | Arguments | Purpose |
| --- | --- | --- |
| `reservations` | `--state-file PATH` | Inspect pending reservations |
| `release-orphan` | `TICKET --state-file PATH` | Release confirmed orphan reservation |
| `reset-client` | `CLIENT_ID --state-file PATH` | Clear persistent suspension |
| `export` | `audit\|violation\|incident\|chat --state-file PATH` | Print structured records |
| `record` | `--kind KIND [--rule LABEL ...] --state-file PATH` | Record incident metadata |
| `generate` | `--policy-file PATH --state-file PATH [--output PATH]` | Generate, validate, and activate restrictive additions |

All administration subcommands accept `--env-file`. Generation defaults to `policy.toml`; state resolves through explicit flag, environment, dotenv, and default. See [administration](../../operations/administration/) before state-changing commands.

## Just recipes

| Recipe | Purpose |
| --- | --- |
| `just test` | Run Python tests |
| `just demo` | Scripted NDA scenario using configured provider/classifier |
| `just demo-local --model NAME` | Local Ollama + Laya scenario; install Laya and serve model first |
| `just proxy` | Start gateway; additional arguments are forwarded |
| `just top` | Open dashboard |
| `just clean` | Reset local development state after processes stop |

Run documentation commands from `docs/site`; see [documentation development](../../contributing/documentation/).
