---
title: Troubleshooting
description: Diagnose rejected payloads, classifiers, quotas, fallback routes, and shared state.
sidebar:
  order: 5
---

Start with the returned `error.code`, the HTTP status, and the state path used by the enforcing process. Automatic retries can obscure policy failures and duplicate work; keep SDK `max_retries=0` while diagnosing.

## Setup fails before the service starts

| Symptom | Check |
| --- | --- |
| Laya is not installed | Install `--extra laya`, choose another configured classifier, or explicitly choose deterministic mode |
| Jev key required | `.env.example` selects Jev; configure `STOPSLOP_JEV_KEY` |
| Ollama requires a model | Supply the exact installed model name |
| Local URL rejected | Use loopback HTTP(S), without URL credentials, query, or fragment |
| Policy invalid | Check the schema, duplicate names, output actions, and required budget fields |
| Non-loopback binding rejected | Configure `STOPSLOP_ACCESS_TOKENS` before exposing the listener |

## Request rejected

`unsupported_payload` usually means a framework added fields outside the allowlist, or selected streaming. `unsupported_messages` means the messages contain unsupported roles, content arrays, names, or tool metadata. Use only the [supported request shape](../../start/compatibility/).

`model_not_allowed` requires approving the actual selected main/local/fallback model, not only the client-requested model. If client selection matters, enable `--preserve-model` and approve its choices.

`policy_blocked` is an input decision; inspect matching rule names. Change a rule only after testing intended allowed and prohibited cases. An exception for one detector does not override another block rule.

## Output withheld

`output_blocked` means generated content violated output policy. Usage can still be charged because the provider completed work. Inspect output rules and audit records. `device_blocked` means a persistent authenticated identity suspension; investigate and then use `reset-client` if appropriate.

`unsupported_response` means the provider returned a shape outside the text-chat contract. This includes tool calls, audio, and non-string generated fields. Verify provider settings and a minimal non-streaming request.

## Budget exceeded below the visible cap

Admission includes pending reservations and a conservative estimate for the new request. Input estimation uses UTF-8 bytes plus message overhead; omitted `max_tokens` reserves 1,024 output tokens. Lower the output allowance, shorten history, or inspect reservations. Check both chat and hosted classifier model budgets.

Rolling `limit` is tokens per second, multiplied by the window. Fixed quota resets use the timezone-aware anchor, not the operator's local timezone. An unscoped quota also applies to fallback models.

## Fallback does not happen

Check `on_exhaustion = "fallback"` on the exhausted budget and the `[budget_fallback]` route. Configure and approve a distinct destination. Alternate admission still must satisfy every applicable budget. A local privacy action cannot use a hosted fallback.

Fallback does not happen on upstream network errors or HTTP failures. A fallback classifier also needs an eligible budget-exhaustion path; it does not suppress general classifier failures.

## Classifier unavailable

Errors such as `jev_unavailable`, `laya_unavailable`, or `llm_unavailable` fail closed. Check dependency availability, keys, URL, network, backend response shape, and assessment budget. Restart after changing runtime settings. A policy-file edit alone cannot change the running classifier.

## Dashboard looks empty or stale

Use the exact same absolute database path in all processes. Relative filenames resolve from each current directory. Chat recording disabled means no new content records, while audit and violation records remain available. If a process crashed, inspect reservations rather than deleting the database to free capacity.

## Policy edit causes 503

Malformed, unsupported, or missing configured policy files fail closed. Restore a valid policy and retry a new request. Use atomic writes to avoid partial-file reloads. Verify generated SQLite additions and any external overlay when the base policy alone appears valid.

See [HTTP reference](../../reference/http/) for statuses and [administration](../administration/) for recovery commands.
