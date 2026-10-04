---
title: Token budgets
description: Configure fixed quotas, rolling token rates, model scopes, and reservation accounting.
sidebar:
  order: 3
---

Budgets limit tokens, not currency prices, request counts, GPU compute, or individual-client allocations. All enforcing processes using the same repository share consumption and pending reservations. Budget scope is determined by the selected model and shared database, not client identity.

## Fixed quota

```toml
[[budgets]]
name = "daily_total"
type = "fixed_quota"
tokens = "total"
limit = 1000000
window_seconds = 86400
reset_at = "2026-01-01T00:00:00Z"
on_exhaustion = "block"
```

This allows up to one million combined input and output tokens per anchored 24-hour window. `reset_at` is a timezone-aware ISO timestamp defining the repeating window boundaries, not a one-time expiry. The example resets at midnight UTC, which differs from local midnight in many timezones.

## Rolling average

```toml
[[budgets]]
name = "rolling_output"
type = "rolling_average"
tokens = "output"
limit = 100
window_seconds = 60
```

For a rolling budget, `limit` is **tokens per second**. The admission ceiling is `limit × window_seconds`: this example permits 6,000 output tokens across the trailing 60 seconds, including reservations. It is not a per-request limit or an average divided by request count. Rolling budgets do not accept `reset_at`.

## Select tokens and models

`tokens` is `input`, `output`, or `total` (default). Omit `models` to cover every model, or list exact model names:

```toml
[[budgets]]
name = "paid_model_daily"
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

Every applicable budget must admit the work. Combine a model-specific cap with an unscoped overall cap to allow cheaper routing while preserving a hard total limit. Independent clients are not assigned separate quotas by these fields.

## Reservation accounting

Admission estimates input as the sum of each message's UTF-8 byte length plus 16 bytes of overhead per message. It reserves the requested `max_tokens` output allowance, or 1,024 when omitted. This estimate is intentionally conservative and is not a provider tokenizer count.

For each applicable budget, completed usage plus all relevant pending reservations plus the new estimate must fit the ceiling. Pending reservations count in every window until completion, including across a fixed-window reset. Transactions serialize concurrent admission across processes.

When work finishes, recognized provider input/output usage replaces the reservation. Successful replies without usage retain estimates. Partial usage reports retain estimates for missing components. Failed calls with reported usage remain charged; failed calls without reported usage release their reservation without chat usage charges. Completed classifier work can still be charged.

Usage events are timestamped at completion. A request spanning a reset may therefore contribute to the completion window. Output rejected by policy still represents generated work and remains charged.

## Semantic assessment usage

When budgets are configured, assessment work participates in reservation accounting under the evaluator's model name, including local Laya. A budget scoped only to a hosted chat model will not cover a differently named Laya model, but an unscoped budget covers both. Assessment accounting uses a synthetic payload containing the questions and conversation, an extra 2,048 characters of overhead, and an output allowance of at least 2,048 (or 128 per question). Successful assessment is charged using estimates rather than provider-reported classifier usage.

Do not assume that a blocked chat request has made no classifier calls. An unscoped quota can prevent assessment or chat work; plan capacity for both. See [classifiers](../classifiers/) and [fallbacks](../fallbacks/).

## Persistence and recovery

History survives temporarily disabling a budget, changing a window, and re-enabling it. Pending reservations survive crashes and do not expire automatically. Inspect them with `stopslop-policy reservations`; release confirmed orphan work only after the owner and remote provider work have stopped. The release command refuses live or unknown owner processes.

An admission failure can occur before the displayed completed usage reaches the cap because outstanding work and the conservative new reservation also count. Reduce `max_tokens`, inspect pending work, or adjust capacity deliberately. See [troubleshooting](../../operations/troubleshooting/).
