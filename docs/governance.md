# Governance and operating limits

## Enforcement

`policy.toml` uses `type = "builtin.email"` and other `builtin.` detector types for built-ins (including regex detectors) and generic `type = "regex"`/`"semantic"` rules with optional names. Legacy IDs remain supported internally and on import. It provides deterministic and semantic rules, output policies, approved models, token budgets and agent operation permissions. Configuration reload is validated before admission. In-flight input/output checks and all their quota reservations use the admitted policy snapshot, including semantic output assessments.

Input and generated text are inspected; reasoning and refusal fields in every choice are included. Nontext/tool-call responses and malformed payloads fail closed. Regex matching has a 50 ms per-pattern timeout. The gateway authenticates before reading/parsing request bodies and bounds both requests and provider responses.

Anonymous filtering covers detected spans only. Unknown names, obfuscations and formats can be missed. Tokens must be preserved exactly for restoration. Semantic filtering removes the entire flagged message because the classifier does not identify spans. Output checks happen before restoration so a caller can recover its own filtered values.

## State and quotas

Every runtime uses one SQLite repository (`STOPSLOP_STATE_FILE`, default `stopslop.sqlite3`). All persistent telemetry and optional content logs live there. Atomic transactions coordinate local gateway workers and SDK processes. Original databases using `control_state` retain their quotas and suspensions; new telemetry tables are created alongside that state. Use an absolute path to avoid processes accidentally selecting different databases.

Completed usage remains available across quota reloads and restarts. Fixed limits count within anchored intervals; rolling limits count tokens per second averaged across the window. Outstanding reservations count in every window until completion, including crash reservations. Failed responses with reported consumption are charged and attributed to their client. Failures without usage release admission; provider work may still have consumed resources that were not reported.

Input reservations estimate UTF-8 bytes plus per-message overhead; output reservations use `max_tokens`, defaulting to 1024 under budgets. Chat usage reconciles provider counts when available. Semantic assessments use conservative estimates. These are token budgets, with no monetary pricing, GPU-time enforcement or distributed persistence guarantees.

`Repository` isolates persistence. Injecting another implementation is supported, but transaction semantics must preserve atomic quota admission. SQLite remains a local-disk backend. Remote agents can share one HTTP gateway's control decisions instead of sharing SQLite over a network filesystem.

## Budget fallback

Model-scoped limits can set `on_exhaustion = "fallback"` and use `[budget_fallback] route = "local"` or `"fallback"`. Local uses the configured loopback model; fallback uses the configured trusted provider. Models must remain approved. Admission retries once against every original budget, so global limits and fallback quotas cannot be bypassed. Input filtering and output protection remain enabled; local-only rules cannot become cloud requests. Budget decisions precede generation; provider errors never initiate another model call.

The optional `classifier` in `budget_fallback` explicitly selects semantic assessment when a paid evaluator is quota-bound, including generated-output assessment. Missing classifiers and invalid assessments fail closed. Quota snapshots remain fixed for each admitted request. Usage is recorded for the actual destination and assessment models. The shipped policy scopes its daily limit to the main model and enables a local fallback; operators must configure that installed local model. Add an unscoped hard cap if total usage must remain bounded across every model.

## Agent tool, MCP and memory access

Permission rules identify authenticated clients, kind, resource, operation and allow/deny. Deny takes precedence; absence of an allow denies. The bearer token supplies identity. Suspended identities cannot authorize operations. The authenticated `/v1/authorize` endpoint and `AgentGuard` callbacks cover tool calls, MCP calls and memory read/write/delete. Audit records retain the decision and client identity without tool arguments or memory contents.

Integrate the guard directly before each side effect. Registered resource names must map to trusted capabilities. This is an integration API, not an OS sandbox, generic MCP protocol proxy, or automatic interception of every memory library. Calls outside the guard are outside enforcement. A successful permission check does not prove that a callback executed safely.

## Reporting and chat logs

Sessions and metrics, full timestamped violation history, completion audits and incident metadata are stored in SQLite. `slopstop-top` offers Overview, Violations and Chats tabs with list/detail scrolling. Audit export writes JSON to stdout; there are no automatic export files. The monitor is read-only.

Content logging is enabled by default. Disable it with `--no-log-chats` / `STOPSLOP_LOG_CHATS=false`. It stores original inputs and delivered replies, including restored caller data. A blocked attempt records input and status, with no delivered response. Authorization headers, configured provider credentials and replacement dictionaries are excluded. Inputs can themselves contain credentials or personal information; logging does not redact those originals. Protect the database through deployment file permissions. Existing logs remain when logging is turned off.

## Incident-driven controls

Incident recording, optional generated policies and their activation use the same repository. Generation is an explicit administrative call, bounded to the latest 100 incident metadata records and one provider request. It accepts only restrictive new IDs and escaped literals or semantic descriptions. It cannot replace base controls, add exemptions, change allowed models, budgets or permissions, or disable output inheritance. Invalid generations preserve the prior overlay; consumed generation tokens are still accounted.

Externally managed policy feeds remain supported as configuration files. Text signatures recognize selected prompt-injection, deserialization, shell-execution and unsafe model-loading constructs. They neither execute code nor scan model repositories. Quoted code can match; real security controls must also constrain execution in the host application.

## Validation

The automated suite uses mocked providers and classifiers. It checks positive/negative enforcement cases, admission across processes, reload races, persisted consumption, failure accounting, request limits, regex timeouts, content logging controls, permission callbacks and TUI pagination. Passing tests does not establish real classifier precision/recall or production exploit coverage. Performance and real-model accuracy still need measurement on the intended deployment; future scalability is outside this change.
