---
title: Monitor usage and violations
description: Use the terminal dashboard, response headers, and structured exports.
sidebar:
  order: 3
---

## Open the dashboard

```sh
uv run --no-sync --package stopslop-top stopslop-top --state-file /absolute/path/stopslop.sqlite3
```

Use the enforcing service's state path. A different working directory with a relative filename can show a different database.

| View | Contents |
| --- | --- |
| Overview | Sessions, active controls, quota usage, telemetry |
| Violations | Timestamped persistent history, newest first |
| Chats | Recorded requests and delivered replies; Enter opens details |
| Graphs | Live request and token rates, mean call latency, failed-call percentage, pending calls, highest active quota usage |

The persistent violation browser is not limited to the latest 1,000 records, even though a runtime session keeps a bounded recent list in its own telemetry.

## Keyboard controls

| Key | Action |
| --- | --- |
| Tab or 1 / 2 / 3 / 4 | Switch view |
| Arrow keys | Navigate or scroll |
| Page Up / Page Down | Move by page |
| Home / End | Move to start or end |
| Enter | Open selected details |
| Escape | Return from details |
| Q | Exit |

Detail panes scroll independently. Timestamps include the local UTC offset. The monitor opens SQLite read-only.

For redirected output or a one-time snapshot:

```sh
uv run --no-sync --package stopslop-top stopslop-top --once --state-file /absolute/path/stopslop.sqlite3
```

The refresh interval defaults to 0.5 seconds and can be changed with `--interval`.

Graphs keep the latest 120 samples while the monitor is open, including while browsing other tabs. Samples are at least one second apart; slower refresh intervals also slow sampling. Rates begin after the first sample and latency and failure percentage describe calls finished since the previous sample. Quota usage includes reservations. Charts stack vertically in narrow terminals and can be scrolled.

## Request-level observability

Successful chat responses identify input action, matching rules, semantic risks, output action, selected model, and budget fallback through `X-StopSlop-*` headers. See [HTTP reference](../../reference/http/) for the complete header table.

Use `--json-logs` on the gateway for structured console output. The authoritative persistence records remain in SQLite; a console log is not a replacement for shared quota state.

## Export records

```sh
stopslop-policy export audit --state-file /absolute/path/stopslop.sqlite3
stopslop-policy export violation --state-file /absolute/path/stopslop.sqlite3
stopslop-policy export incident --state-file /absolute/path/stopslop.sqlite3
stopslop-policy export chat --state-file /absolute/path/stopslop.sqlite3
```

Exports print structured records to standard output. Chat exports can include sensitive original content. Use the [administration guide](../administration/) for suspension and reservation recovery.
