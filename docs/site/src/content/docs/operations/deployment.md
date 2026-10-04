---
title: Deploy the gateway
description: Operate an authenticated gateway with explicit state, privacy, and routing boundaries.
sidebar:
  order: 1
---

## Prepare a deployment

1. Install the workspace on Python 3.14 and choose a tested text-chat provider and classifier.
2. Approve exact main, local, and fallback model names in policy.
3. Choose an absolute state database path on a local disk. Give the service account access to its directory and SQLite journal files.
4. Configure unique gateway access tokens; keep provider credentials server-side.
5. Decide whether original chat content may be stored. Chat recording is enabled by default.
6. Test clean, filtered, blocked, output-rejected, quota-exhausted, and fallback requests.
7. Place remote traffic behind HTTPS and your normal service supervision.

## Authentication and binding

```dotenv
STOPSLOP_ACCESS_TOKENS={"service-a":"replace-with-secret-a","service-b":"replace-with-secret-b"}
STOPSLOP_STATE_FILE=/absolute/path/stopslop.sqlite3
STOPSLOP_LOG_CHATS=false
```

```sh
uv run --no-sync --package stopslop-proxy stopslop-proxy --host 0.0.0.0 --port 8000 --json-logs
```

Use an absolute Windows path on Windows. The CLI rejects non-loopback binding without configured tokens. Remote connections require HTTPS at a reverse proxy; the CLI does not provide a TLS certificate setup. An application embedding `create_app` must enforce its own deployment and listener constraints.

Client tokens and upstream provider keys serve different purposes. Restart to change runtime token configuration. Avoid passing secrets as CLI flags when environment or managed secret injection is available.

## Privacy boundaries

Input `local` constrains chat routing, but classifier selection is independent. A hosted classifier can inspect message content even when chat is routed locally. Use a local classifier for deployments requiring all assessment and chat to stay local.

Input filtering hides detected spans from the chat provider, but original input may remain in the state database when chat recording is enabled. Review [storage and privacy](../storage/).

## Health and limits

`GET /health` reports listener health and the configured runtime detector mode. It does not actively test upstream credentials or classify content. Use a representative protected request for a readiness check, with awareness that it can incur usage.

Default body limits are 2 MiB requests and 4 MiB responses. A separate combined message-text limit is 1 MiB. Tune runtime HTTP byte limits without assuming the supported message shape changes.

## Shared state and scaling

Multiple local enforcement processes can share one database and atomically serialize admissions. Remote clients should use one central gateway. SQLite on a network share is outside the supported local-disk deployment model. A distributed deployment requires a replacement repository with the full protocol and transactional guarantees; that implementation is not shipped.

## Failure behavior

Invalid policy updates and unavailable classifiers fail closed. Provider failures do not trigger budget fallback. Reservations survive process crashes and need deliberate orphan recovery. Cancellation may not mean remote work has stopped; confirm this before manual release.

The service does not implement billing, automated token rotation, per-client budget allocation, or a callback sandbox. Plan those responsibilities in the surrounding application.
