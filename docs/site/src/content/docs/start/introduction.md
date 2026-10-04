---
title: Introduction
description: Understand the policy engine and choose an integration boundary.
---

StopSlop is a control layer around OpenAI-compatible text Chat Completions. It evaluates messages before forwarding them, reserves token capacity, selects an approved destination, and checks the generated response before delivery.

## The request lifecycle

1. Authenticate the client when access tokens are configured and reject suspended identities.
2. Reload the current policy and validate the request shape and model.
3. Scan message text with deterministic detectors and configured semantic rules.
4. Resolve input actions using **block > local > filter > allow**. Redact filter matches with request-local anonymous tokens.
5. Admit work against applicable budgets. An eligible exhaustion may select one alternate route, whose approvals and budgets are checked again.
6. Send the prepared request to the provider.
7. Inspect generated text using the same request's policy snapshot. Withhold, redact, or warn according to output policy.
8. Restore exact anonymous tokens locally, account for usage, and write telemetry and optional chat records.

Semantic assessment may itself require budget admission before chat work. A chat failure does not undo already completed assessment work.

## Choose a boundary

| Pattern | Use it when | Enforcement runs |
| --- | --- | --- |
| HTTP gateway | Clients run remotely, use another language, or use asynchronous Python | In the gateway process |
| `PolicyRouter` | A synchronous Python SDK client should enforce policy without a server | In the application process |
| `AgentGuard` | A trusted integration needs tool, MCP, or memory authorization | Locally or through the authorization endpoint |

Gateway and SDK enforcement share the policy implementation. For shared quotas and suspensions, point every enforcing process at the same absolute SQLite path on a local disk. Remote agents can share one gateway; the database is not a distributed coordination service.

## Package responsibilities

| Workspace package | Purpose | Entry point |
| --- | --- | --- |
| `stopslop` | Policy engine, transport, repository, administration | `stopslop-policy` |
| `stopslop-proxy` | FastAPI HTTP gateway | `stopslop-proxy` |
| `stopslop-demo` | Interactive and scripted chat | `stopslop-demo` |
| `stopslop-top` | Read-only terminal dashboard | `stopslop-top` |

The core package has no FastAPI or terminal UI dependency. Python 3.14 is required by the workspace.

## Enforcement boundaries

Detectors recognize particular formats and classifier decisions depend on the selected backend. Treat policies as controls that require evaluation with your own allowed and prohibited examples. Agent permissions authorize registered capabilities; callbacks still need their own argument validation and execution safeguards.

See [supported usage](../compatibility/) for protocol limits and [deployment](../../operations/deployment/) for operating assumptions.
