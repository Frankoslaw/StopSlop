---
title: Runtime configuration
description: Complete environment, CLI, Python setting, default, and precedence reference.
sidebar:
  order: 1
---

Every runtime setting has one Python name, one `STOPSLOP_` environment variable, and a matching kebab-case CLI flag on the gateway and demo. For example, `base_url` maps to `STOPSLOP_BASE_URL` and `--base-url`.

## Precedence

**Explicit flag or Python override > process environment > selected dotenv file > built-in default.** Reading a dotenv file does not mutate the process environment. The default dotenv path is `.env`; use `--env-file PATH` or `Settings.load(env_file=...)` to change it.

Boolean environment values accept `true`, `false`, `1`, and `0` (case-insensitive). Boolean CLI settings offer `--name` and `--no-name`. Runtime configuration is fixed for a running process. Policy enforcement decisions reload separately.

## Upstream

| Python name | Environment variable | Default / purpose |
| --- | --- | --- |
| `provider` | `STOPSLOP_PROVIDER` | `nvidia`; choices `nvidia`, `openai`, `ollama` |
| `base_url` | `STOPSLOP_BASE_URL` | Selected provider's `/v1` endpoint |
| `model` | `STOPSLOP_MODEL` | Selected provider's model; Ollama requires a value |
| `key` | `STOPSLOP_KEY` | Empty; hosted provider credential |

| Provider | Default URL | Default model |
| --- | --- | --- |
| NVIDIA | `https://integrate.api.nvidia.com/v1` | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4.1-mini` |
| Ollama | `http://127.0.0.1:11434/v1` | No default; exact installed model required |

Set the four upstream values together when switching providers, especially when the selected dotenv file already supplies an endpoint or model. Explicit URL/model values remain in effect regardless of provider selection. Ollama supplies a dummy `ollama` key when the configured key is empty. Defaults describe the code's choices, not provider model availability guarantees.

## Policy and assessment

| Python name | Environment variable | Default / purpose |
| --- | --- | --- |
| `policy_file` | `STOPSLOP_POLICY_FILE` | Empty; discover `policy.toml` when eligible |
| `policy` | `STOPSLOP_POLICY` | `block`; no-file modes: `block`, `redirect`, `filter`, `passthrough` |
| `rules_file` | `STOPSLOP_RULES_FILE` | Empty; custom detector catalog, mutually exclusive with policy file |
| `dynamic_policy_file` | `STOPSLOP_DYNAMIC_POLICY_FILE` | Empty; restrictive additive overlay; requires base policy |
| `deterministic` | `STOPSLOP_DETERMINISTIC` | `false`; skip semantic checks explicitly |
| `classifier` | `STOPSLOP_CLASSIFIER` | `laya`; choices `laya`, `jev`, `llm`, `ollama` |
| `fallback_classifier` | `STOPSLOP_FALLBACK_CLASSIFIER` | Empty; independent eligible budget assessment backend |
| `laya_model` | `STOPSLOP_LAYA_MODEL` | `convaiinnovations/laya` |
| `laya_device` | `STOPSLOP_LAYA_DEVICE` | `cpu` |
| `jev_key` | `STOPSLOP_JEV_KEY` | Empty; Jev credential |
| `jev_base_url` | `STOPSLOP_JEV_BASE_URL` | `https://api.typesafe.ai/v1` |
| `jev_model` | `STOPSLOP_JEV_MODEL` | `jev-latest` |

Automatic policy discovery requires no selected `policy_file`, `rules_file`, or explicit `policy` setting and a `policy.toml` in the current working directory. `--policy` names are not the TOML rule actions. Legacy provider-specific environment names and `MAIN_*` variables are not supported.

## Alternate destinations

| Python name | Environment variable | Default / purpose |
| --- | --- | --- |
| `local_base_url` | `STOPSLOP_LOCAL_BASE_URL` | `http://127.0.0.1:11434/v1`; loopback required |
| `local_model` | `STOPSLOP_LOCAL_MODEL` | Empty; exact local model |
| `local_key` | `STOPSLOP_LOCAL_KEY` | `local` |
| `fallback_base_url` | `STOPSLOP_FALLBACK_BASE_URL` | Empty; trusted hosted destination |
| `fallback_model` | `STOPSLOP_FALLBACK_MODEL` | Empty; exact alternate model |
| `fallback_key` | `STOPSLOP_FALLBACK_KEY` | Empty; alternate provider credential |

Budget routing is selected by `[budget_fallback]` in policy; these settings supply destinations. See [fallbacks](../../policies/fallbacks/).

## State, identity, and limits

| Python name | Environment variable | Default / purpose |
| --- | --- | --- |
| `state_file` | `STOPSLOP_STATE_FILE` | `stopslop.sqlite3`; prefer a shared absolute path |
| `log_chats` | `STOPSLOP_LOG_CHATS` | `true`; record original inputs and delivered replies |
| `access_tokens` | `STOPSLOP_ACCESS_TOKENS` | Empty; JSON identity-to-unique-bearer-token map |
| `client_token` | `STOPSLOP_CLIENT_TOKEN` | Empty; identity token used by SDK/demo |
| `preserve_model` | `STOPSLOP_PRESERVE_MODEL` | `false`; honor approved client model selection |
| `max_request_bytes` | `STOPSLOP_MAX_REQUEST_BYTES` | `2097152` (2 MiB), positive integer |
| `max_response_bytes` | `STOPSLOP_MAX_RESPONSE_BYTES` | `4194304` (4 MiB), positive integer |
| `timeout` | `STOPSLOP_TIMEOUT` | `120.0` seconds; finite positive number |

`timeout` configures HTTP timeout behavior, not a single total end-to-end deadline for classification, chat, and output checks. Listener `--host`, `--port`, and `--json-logs` are gateway-only CLI options, not `Settings` fields.

## Automatic generation

| Setting | Environment | Default |
| --- | --- | --- |
| `autogen` | `STOPSLOP_AUTOGEN` | `false` in code; `true` in shipped dotenv; `--autogen` / `--no-autogen` |
| `autogen_output_file` | `STOPSLOP_AUTOGEN_OUTPUT_FILE` | `policy.dyn.toml` |

The `ollama` classifier uses the local endpoint/model/key independently of the main provider. The shipped fallback classifier is `ollama` using `qwen3:0.6b`. See [administration](../../operations/administration/) for scheduling and activation.

## Switch to hosted chat

Keep Laya for local assessment and change all four upstream values in `.env`, for example:

```dotenv
STOPSLOP_PROVIDER=nvidia
STOPSLOP_BASE_URL=https://integrate.api.nvidia.com/v1
STOPSLOP_MODEL=nvidia/nemotron-3-nano-omni-30b-a3b-reasoning
STOPSLOP_KEY=your-provider-key
```

Approve the model in `policy.toml`, then run `just demo`. Qwen remains available for local privacy routing, eligible budget fallback, and deferred rule generation. `just demo-local` always selects Ollama and Laya.
