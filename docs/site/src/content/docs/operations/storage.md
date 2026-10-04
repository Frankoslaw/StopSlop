---
title: Storage and privacy
description: Understand SQLite state, original chat recording, persistence, and repository injection.
sidebar:
  order: 2
---

`STOPSLOP_STATE_FILE` or `--state-file` selects the SQLite database, defaulting to `stopslop.sqlite3` relative to the current directory. Use the same absolute path for gateway, SDK, demo, monitor, and administration to share state.

## What the database stores

| Data | Purpose |
| --- | --- |
| Completed usage and pending reservations | Shared quota admission |
| Client suspensions | Persistent output enforcement |
| Session telemetry | Dashboard and runtime reporting |
| Audit records and all violations | Enforcement history |
| Incident metadata and generated policy | Explicit restrictive adaptation |
| Chat records, when enabled | Original inputs and delivered replies |

Runtime state does not require separate JSON snapshots, log files, incident feeds, or session directories. SQLite may create transient transaction journals. The monitor opens the database read-only.

## Chat recording

Recording is **enabled by default**. Disable new content records using:

```dotenv
STOPSLOP_LOG_CHATS=false
```

Or add `--no-log-chats` to the gateway or demo. `--log-chats` explicitly enables it again.

Recorded chats contain original input messages, delivered replies, timestamps, selected model, action, and client identity. Blocked attempts record a status without a delivered reply. Authorization headers, provider keys, and request-local replacement dictionaries are not recorded by this API.

Original inputs can contain sensitive data even if the provider receives redacted content. Protect the database and backups accordingly. Disabling recording stops new chat records; it does not delete previous records or disable audit and violation metadata.

## Retention and backup

The project does not provide an automatic retention scheduler or migration from older state formats. Define retention and backup policy in your deployment. Prefer a SQLite-aware backup operation, or stop all writers before copying the database. Do not copy only the main file while transaction sidecars may contain active state.

Quota history is retained when limits change or are disabled. An incomplete reservation is not aged out automatically. See [administration](../administration/) before removing state.

## Replace persistence

`Repository` in `stopslop.repository` is the persistence boundary. `Runtime`, `Policy`, `PolicyRouter`, and `create_app` accept a repository injection. Implementations must support the complete protocol, including atomic transactions serializing admission, state loading/saving, append/query/export behavior, sessions, and generated policy.

When injecting a repository, your integration owns its lifecycle. Default runtimes close repositories they create. An in-memory repository is suitable for isolated work, but does not coordinate separate processes or persist crash recovery state.

## Reset local development state

Stop the gateway, demo, and dashboard before running `just clean`. The recipe removes workspace SQLite databases and sidecars, legacy telemetry and session files, and generated `policy.dyn.*` exports while preserving `.env`, `policy.toml`, source, and dependencies. A configured custom state path must be inside the workspace for cleanup. This is a development reset, not a production retention command.
