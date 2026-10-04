---
title: Semantic classifiers
description: Select Jev, local Laya, or upstream LLM assessment and understand their failure behavior.
sidebar:
  order: 5
---

Deterministic rules scan text locally. Semantic rules require an evaluator selected through runtime settings, independently of `policy.toml`.

| Backend | Configuration | Operational behavior |
| --- | --- | --- |
| `laya` | Optional Laya dependency; `STOPSLOP_LAYA_MODEL`, `STOPSLOP_LAYA_DEVICE` | Local assessment, CPU default |
| `jev` | `STOPSLOP_JEV_KEY`, URL, and model | Hosted assessment through Jev |
| `llm` | Main upstream URL, model, and key | Uses configured chat provider for assessment |

## Laya

```sh
uv sync --all-packages --extra laya
```

```dotenv
STOPSLOP_CLASSIFIER=laya
STOPSLOP_LAYA_MODEL=convaiinnovations/laya
STOPSLOP_LAYA_DEVICE=cpu
```

The demo prepares Laya before its spinner starts. Allow for dependency installation, model availability, and startup time. Local assessment avoids hosted classifier calls; it does not make a hosted chat route local.

## Jev

```dotenv
STOPSLOP_CLASSIFIER=jev
STOPSLOP_JEV_BASE_URL=https://api.typesafe.ai/v1
STOPSLOP_JEV_MODEL=jev-latest
STOPSLOP_JEV_KEY=replace-with-classifier-key
```

The shipped `.env.example` selects Jev. Its key is separate from the chat provider key. Semantic assessment sends content for classification; review that trust boundary alongside chat routing.

## Upstream LLM

```dotenv
STOPSLOP_CLASSIFIER=llm
```

This backend uses the main provider settings. Assessment and chat can compete for the same model-scoped budget. If using an exhausted hosted model, configure an independent fallback classifier with enough capacity.

## Failure and deterministic mode

Classifier failures block forwarding or output delivery. A fallback classifier handles eligible budget exhaustion, not general network outages or malformed classifier replies. Classifier selection is fixed for the running process; restart to change it.

```sh
uv run --no-sync --package stopslop-demo stopslop-demo --deterministic "Explain rain."
```

Deterministic mode explicitly disables semantic checks while retaining deterministic enforcement. Document this choice in your deployment and test coverage: a semantic confidentiality rule cannot protect a deterministic-only run.

## Evaluate rule quality

Build fixtures with clear violations, safe public discussion, fictional examples, quoted material, and obfuscated forms relevant to your application. Review both false positives and missed violations before changing thresholds. Threshold percentages are classifier risk scores; they are not a guarantee of calibrated probability across backends.
