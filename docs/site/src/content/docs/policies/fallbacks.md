---
title: Fallbacks and local routing
description: Route privacy-sensitive requests locally and handle eligible budget exhaustion once.
sidebar:
  order: 4
---

StopSlop has distinct mechanisms for local privacy routing, budget exhaustion, and legacy no-file redirect mode. Budget fallback is an admission decision before chat forwarding. It is not an automatic retry after an upstream failure.

## Local privacy routing

An input `action = "local"` sends the request to `STOPSLOP_LOCAL_BASE_URL` using `STOPSLOP_LOCAL_MODEL` and `STOPSLOP_LOCAL_KEY`. The local URL must be a loopback HTTP(S) endpoint without embedded credentials, query, or fragment. The model must be configured and approved.

```dotenv
STOPSLOP_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
STOPSLOP_LOCAL_MODEL=llama3.2
STOPSLOP_LOCAL_KEY=local
```

Add the exact installed model name to `allowed_models`. Filtering and output inspection still apply. A local-only privacy request cannot switch to a hosted fallback provider.

## Continue after a model budget is exhausted

```toml
version = 1
default_action = "filter"
allowed_models = ["your-paid-model", "llama3.2"]

[[budgets]]
name = "paid_daily"
type = "fixed_quota"
tokens = "total"
models = ["your-paid-model"]
limit = 1000000
window_seconds = 86400
reset_at = "2026-01-01T00:00:00Z"
on_exhaustion = "fallback"

[budget_fallback]
route = "local"
```

Set `route = "fallback"` for an independent hosted destination instead:

```dotenv
STOPSLOP_FALLBACK_BASE_URL=https://trusted-provider.example/v1
STOPSLOP_FALLBACK_MODEL=your-approved-alternate-model
STOPSLOP_FALLBACK_KEY=replace-with-provider-key
```

The hosted URL is a placeholder; replace it with a verified compatible endpoint and add the alternate model to `allowed_models`. StopSlop does not infer prices. Select a cheaper or free destination yourself.

## What is preserved

On eligible exhaustion, admission tries one alternate destination. Authentication, redactions, model approvals, input constraints, and output checks remain active. All applicable budgets are checked again for the alternate model.

- A budget without `models` still applies to the alternate destination.
- The alternate model's own quota can reject admission.
- A destination with the same model name is rejected as `budget_fallback_same_model`.
- Missing or unapproved destinations fail closed.
- A privacy-local request cannot escape to the hosted fallback route.
- A provider error does not trigger budget fallback or provider retries.

## Keep assessment available

If the hosted classifier's budget is exhausted, select an independent backend:

```dotenv
STOPSLOP_FALLBACK_CLASSIFIER=laya
```

Install Laya with `uv sync --all-packages --extra laya`. Jev or LLM can also be selected, but their credentials and budgets must admit the work. The alternate backend is used for eligible budget exhaustion, not arbitrary classifier failures. Semantic checks stay active unless you explicitly select deterministic mode.

## Observe the chosen destination

Successful responses include `X-StopSlop-Budget-Fallback` and `X-StopSlop-Model`. `X-StopSlop-Action` describes the input privacy decision independently. Audit records, telemetry, and chat records also identify the destination. Test your alternate route directly before relying on it.

## Legacy no-file redirect mode

Without a policy file, `STOPSLOP_POLICY=redirect` routes matched detector content to the configured hosted fallback and requires its URL, model, and key. The other no-file modes are `block`, `filter`, and `passthrough`. These names are runtime detector modes, not the TOML input action vocabulary. Prefer an explicit policy file for per-rule decisions, budgets, model approvals, and output policy.
