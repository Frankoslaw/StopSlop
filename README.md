# StopSlop

A hybrid AI control layer: deterministic privacy/security rules, semantic checks, output filtering, token quotas, and authenticated agent permissions. Integrate through an OpenAI-compatible HTTP gateway or an `httpx` SDK transport. Python 3.14 is required.

## Run

```powershell
uv sync --all-packages --extra laya
uv run --no-sync pytest -q
uv run --no-sync --package stopslop_demo stopslop-demo --scenario nda --policy-file policy.toml --log-chats
uv run --no-sync --package stopslop slopstop-top
```

Copy `.env.example` to `.env` and set `STOPSLOP_MAIN_KEY`. Laya runs locally on CPU and downloads weights on first use. `classifier = "jev"` requires its own key; `classifier = "llm"` uses the configured chat provider. `--deterministic` explicitly disables semantic rules. Classifier failures block forwarding; backends never silently substitute for one another.

The demo checks each turn and its generated reply. `--scenario nda` runs invented allowed, filtered and blocked examples. Interactive and single-prompt chat never retry; scenario retries only transient chat HTTP 502/503/504 responses. Tests use mock providers and classifiers, with no API calls or credentials.

## Edit policy

`policy.toml` is the editable policy catalog. It includes privacy controls, historical attack signatures, semantic thresholds, approved models, output checks, a daily token budget, and example tool/MCP/memory permissions. Add installed local model names to `allowed_models` before using them.

```toml
version = 1
classifier = "laya"
default_action = "filter"
allowed_models = ["your-approved-model"]

[[rules]]
type = "regex"
name = "internal_project"
pattern = '\bProject Kestrel\b'
action = "block"

[[rules]]
type = "semantic"
name = "confidential"
description = "Do not disclose non-public company information. Public information is allowed."
threshold = 75
# allow, filter, local or block
action = "block"

[[budgets]]
name = "daily_total"
type = "fixed_quota"
tokens = "total"
limit = 1000000
window_seconds = 86400
reset_at = "2026-01-01T00:00:00Z"
```

Built-in rules use `type = "builtin.email"`, `"builtin.phone"`, `"builtin.secret"`, and the other prefixed detector names, including built-in regex detectors. Custom rules use `type = "regex"` with `pattern`, or `type = "semantic"` with `description`. An optional `name` provides a stable label in audit records; unnamed custom rules receive a stable label derived from their definition. Multiple generic regex rules are supported. Output overrides can reference a named rule using its type and name. Budgets use `name` plus their budget `type`; permissions use `name` plus `type = "tool"`, `"mcp"`, or `"memory"`. Legacy unprefixed detector types and `id`/`kind` policy fields remain readable.

Policies reload before the next request. Invalid updates fail closed. Every in-flight request retains its input/output policy and budget snapshot. JSON policies remain readable for existing integrations. Deterministic rules use a 50 ms regex timeout per rule. Built-in detectors remain active when their entries are omitted; explicitly set `action = "allow"` to disable a category.

Input precedence is block > local > filter > allow. Filtering sends unique anonymous tokens to the provider and restores exact returned tokens locally. Output is inspected before restoration; generated secrets or prohibited replies are withheld. Tool calls, streaming and multimodal chat responses are rejected by the text-chat gateway. Configure local routing with `STOPSLOP_LOCAL_MODEL` and a loopback `STOPSLOP_LOCAL_BASE_URL`.

## One database

`STOPSLOP_STATE_FILE` / `--state-file` selects one SQLite database, default `stopslop.sqlite3`. Use the same absolute path for the gateway, SDK/demo, monitor and administration. It contains quota consumption, pending reservations, suspensions, sessions, audit records, all violations, incident metadata, generated policies and opted-in chats. No runtime JSON snapshots, log files, incident feeds or session directories are created. SQLite may use a transient rollback journal during transactions.

The `Repository` protocol in `stopslop.repository` is the persistence boundary. `Runtime`, `Policy`, `PolicyRouter` and `create_app` accept an injected repository. Replacement implementations must provide atomic transactions that serialize admission. SQLite is for a local disk; this project does not implement a distributed database.

Quota history survives disabling, shortening and re-enabling limits. Failed calls with reported usage remain charged; calls with no reported usage release their reservation. Pending reservations survive crashes and do not expire automatically. Usage is counted when work finishes. These are token limits, not currency prices or GPU compute limits.

## Continue on a cheaper or free model when a budget runs out

Budgets default to `on_exhaustion = "block"`. Set `"fallback"` on a model-scoped budget and select a destination:

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
route = "local"       # Free local model: STOPSLOP_LOCAL_* settings
classifier = "laya"   # Optional classifier for exhausted paid semantic assessments
# route = "fallback" # Cheaper/free provider: STOPSLOP_FALLBACK_* settings
```

The shipped policy enables local fallback for the main model's daily budget. Configure `STOPSLOP_LOCAL_MODEL` to an installed model and add that exact name to `allowed_models`. For a provider fallback, configure its URL, key and model through `STOPSLOP_FALLBACK_BASE_URL`, `STOPSLOP_FALLBACK_KEY` and `STOPSLOP_FALLBACK_MODEL`, and change the route to `"fallback"`. Provider prices are an operator choice; StopSlop does not infer which model is cheaper or free.

Admission tries the alternate destination once before making a chat-provider call. It preserves redactions, model approvals, authentication and output checks. Every configured budget is checked again for the new model: an overall budget without `models` remains a hard cap, and a fallback model's own quota can still reject the request. A local-only privacy rule cannot switch to a cloud provider. Missing/unapproved destinations fail closed; provider errors do not trigger a budget fallback or retries.

If a hosted classifier uses the exhausted budget, configure an available fallback classifier (usually local Laya) to keep semantic input/output checks active. Classifier errors remain blocking. Response headers `X-StopSlop-Budget-Fallback` and `X-StopSlop-Model`, audit records, metrics and opted-in chat logs identify the fallback destination. `X-StopSlop-Action` continues to describe the input privacy action.

## Browse the TUI

Run `slopstop-top --state-file PATH` in another terminal:

- **Overview**: sessions, controls, quota usage and telemetry; scroll to see the full dashboard.
- **Violations**: timestamped history, newest first, with no 1,000-record browsing cap.
- **Chats**: recorded requests and delivered replies. Select a row and press Enter to read it.

Tab or 1/2/3 switches views. Arrows, Page Up/Down and Home/End navigate. Enter opens details; Escape returns; Q exits. Details scroll independently. Timestamps include the local UTC offset. `--once` or redirected output prints a dashboard snapshot. The monitor opens SQLite read-only.

Chat content is **off by default**. Enable `--log-chats` on the gateway or demo, or set `STOPSLOP_LOG_CHATS=true`. This stores original input messages, delivered replies, timestamps, model, action and client identity. Blocked attempts have a status and no delivered reply. Headers, provider keys and anonymous replacement dictionaries are not recorded. Original inputs can contain sensitive data; treat the database accordingly. Turning logging off stops new content records but does not remove existing ones.

## Gateway and agent permissions

```powershell
uv run --no-sync --package stopslop stopslop --policy-file policy.toml --preserve-model --log-chats
```

Clients send non-streaming text chat to `http://127.0.0.1:8000/v1/chat/completions`. Set `STOPSLOP_ACCESS_TOKENS` to a JSON map of client identities to unique bearer tokens. Non-loopback binding requires those tokens. Remote deployments need HTTPS at the gateway or a reverse proxy. Body limits are configurable with `STOPSLOP_MAX_REQUEST_BYTES` and `STOPSLOP_MAX_RESPONSE_BYTES`.

Agent operations use separate deny-by-default permissions. Missing identity or permission denies access; explicit deny wins over allow. The shipped examples permit `demo-agent` to call `search`, call `docs/search` through MCP, and read/write `project/*` memory while denying `project/secrets*`.

```python
from stopslop import AgentGuard

# Remote agents on any host use one gateway for permission decisions.
guard = AgentGuard(client_token, base_url="https://your-gateway")
result = guard.call_tool("search", search_function, query)
result = guard.call_mcp("docs", "search", mcp_search_function, query)
value = guard.read_memory("project", "plan", memory_store.get)
guard.write_memory("project", "plan", value, memory_store.set)
# Or AgentGuard(client_token, policy=your_policy) for local enforcement.
```

`POST /v1/authorize` accepts `{kind, resource, operation}` and the bearer token. Identity always comes from the token, never the body. Supported kinds are tool, MCP and memory; supported operations are call, MCP read, and memory read/write/delete. Resource names identify registered capabilities and logical memory keys. Path traversal is rejected. Integrations must route every operation through the guard and map approved names to trusted callbacks. This authorizes capabilities; it does not sandbox callbacks, inspect tool arguments, or intercept unrelated calls made outside the guard.

## Administration

```powershell
stopslop-policy reservations --state-file stopslop.sqlite3
stopslop-policy reset-client demo-agent --state-file stopslop.sqlite3
stopslop-policy release-orphan RESERVATION_ID --state-file stopslop.sqlite3
stopslop-policy export audit --state-file stopslop.sqlite3
stopslop-policy export violation --state-file stopslop.sqlite3
stopslop-policy export chat --state-file stopslop.sqlite3
stopslop-policy record --kind code_execution --rule shell_execution --state-file stopslop.sqlite3
stopslop-policy generate --policy-file policy.toml --state-file stopslop.sqlite3
```

Generation is explicit and bounded. It sends incident metadata and policy definitions, never raw chats, and saves only validated restrictive additions in SQLite. The next request picks them up. `--output policy.dyn.toml` optionally exports configuration for inspection. `--dynamic-policy-file` loads an externally managed additive policy; generated state does not require that flag.

Existing quota SQLite files remain usable: point `--state-file` at the original file to preserve reservations and suspensions. To import legacy telemetry/feeds, run `stopslop-policy migrate --state-file ORIGINAL.sqlite3 --metrics-file metrics.json --audit-file stopslop.log --incidents-file incidents.jsonl`. Optional `--dynamic-policy-file policy.dyn.json --policy-file policy.toml` imports a validated overlay. Import once; repeated imports duplicate telemetry. Source files are preserved. Old `--metrics-file`, `--log-file` and `--incident-file` runtime flags have been removed.

Orphan release refuses live or unknown owner processes. Check that remote provider work has also stopped before releasing a reservation. See [governance](docs/governance.md) for implementation limits and [requirements](docs/requirements.md) for competition scope.
