---
title: HTTP API and errors
description: Gateway endpoints, response headers, payload contract, and error statuses.
sidebar:
  order: 4
---

## GET /health

```json
{"status":"ok","policy":"block"}
```

`policy` is the runtime no-file mode setting, even when an explicit policy file is active. This is a listener-health response, not a full readiness probe or effective-policy export.

## POST /v1/chat/completions

Send `Authorization: Bearer TOKEN` when access tokens are configured, and a JSON text-chat body:

```json
{
  "model": "your-approved-model",
  "messages": [{"role":"user","content":"Explain rain."}],
  "max_tokens": 96,
  "stream": false
}
```

See [supported usage](../../start/compatibility/) for the exact field allowlist. Success returns a checked Chat Completions JSON response.

### Successful response headers

| Header | Meaning |
| --- | --- |
| `X-StopSlop-Action` | Input privacy action |
| `X-StopSlop-Rules` | Comma-separated input rule labels |
| `X-StopSlop-Risks` | JSON semantic input risk map |
| `X-StopSlop-Output-Action` | Output action |
| `X-StopSlop-Output-Rules` | Comma-separated output rule labels |
| `X-StopSlop-Output-Risks` | JSON semantic output risk map |
| `X-StopSlop-Budget-Fallback` | `none`, `local`, or `fallback` |
| `X-StopSlop-Model` | Actual selected model |

These headers describe successful checked responses; error paths are not required to include them.

## POST /api/chat

Native Ollama text clients can send `model`, `messages`, explicit `stream: false`, and optional `options` containing `temperature`, `top_p`, or a positive `num_predict` (mapped to `max_tokens`). Other fields and options are rejected. Ollama defaults to streaming, so omitting `stream: false` is rejected. Message rules, authentication, model selection, policy enforcement, and response headers match Chat Completions.

The gateway translates requests to its configured `/v1` upstream and returns a checked Ollama-shaped response with `model`, `created_at`, `message`, `done`, `done_reason`, `prompt_eval_count`, and `eval_count`. Provider timing fields are unavailable. Configure the upstream with its OpenAI-compatible base URL. See [Ollama's chat contract](https://docs.ollama.com/api/chat).

## POST /v1/authorize

Requires a configured authenticated identity even when chat is allowed without authentication on loopback. The body contains exactly `kind`, `resource`, and `operation`:

```json
{"kind":"tool","resource":"search","operation":"call"}
```

Success:

```json
{"allowed":true,"client_id":"demo-agent","rules":["approved_search"]}
```

Supported kinds and operations are documented in [agent permissions](../../integrations/agent-permissions/). The identity comes from the token, never a caller-supplied body field. Authorization bodies are limited to at most 8 KiB (or the configured smaller request limit).

## Error envelope

Policy failures return an error object, for example:

```json
{"error":{"code":"policy_blocked","rules":["internal_project"],"risks":{}}}
```

`code` identifies the failure. `rules`, `risks`, `message`, and upstream `status` are present on applicable paths, not every failure. Handle missing optional fields. SDK integrations may raise the client library's HTTP error type rather than `PolicyError` directly.

## Error categories

| Status | Codes | Response |
| --- | --- | --- |
| 400 | `invalid_json`, `unsupported_payload`, `unsupported_messages`, `invalid_model`, `invalid_max_tokens`, `invalid_temperature`, `invalid_top_p`, `invalid_reasoning_budget` | Correct the request shape |
| 400 | `invalid_operation` | Correct kind, resource, operation, or extra fields |
| 400 | `unsupported_endpoint` | In-process router only; use text Chat Completions |
| 401 | `unauthorized_client` | Supply an assigned client bearer token |
| 403 | `policy_blocked`, `output_blocked` | Inspect matched rules; do not retry unchanged |
| 403 | `model_not_allowed` | Approve the selected model or choose an approved one |
| 403 | `operation_denied` | Require an appropriate capability grant |
| 403 | `device_blocked` | Investigate persistent identity suspension |
| 403 | `budget_fallback_not_allowed` | A privacy-local request cannot use a hosted alternate |
| 413 | `payload_too_large` | Reduce HTTP body or combined message content |
| 429 | `budget_exceeded`, `budget_fallback_same_model` | Review capacity, scope, reservations, or distinct alternate model |
| 502 | `upstream_unavailable`, `unsupported_response`, `response_too_large` | Check provider connectivity, reply shape, and response size |
| 503 | `invalid_policy_configuration`, `rule_timeout` | Repair policy or bounded detector patterns |
| 503 | `missing_local_model`, `missing_upstream_key`, `missing_budget_fallback`, `missing_evaluator_key` | Supply required runtime configuration |
| 503 | `jev_unavailable`, `laya_unavailable`, `llm_unavailable` | Restore semantic assessment availability |
| Upstream error status | `upstream_error` | Inspect the returned status; no budget fallback is triggered |

Unknown HTTP gateway routes use normal framework error responses rather than the router-only `unsupported_endpoint` contract. Transport-level exceptions may propagate in the in-process SDK path. The gateway maps upstream HTTP transport failures to `upstream_unavailable`.

## Retry policy

Gateway and in-process enforcement do not retry chat provider calls automatically. Interactive and single-prompt demo chat also do not retry. Scripted demo scenarios may retry only transient chat HTTP 502/503/504 responses, bounded by `--retries`. They do not retry policy decisions or classifier failures.
