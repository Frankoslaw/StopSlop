# StopSlop

An AI control layer for privacy, security, token quotas and authenticated agent permissions. Input and output use the same policy engine through an HTTP gateway or an in-process SDK transport. Python 3.14 is required.

## Documentation site

The [Astro + Starlight documentation](docs/site/README.md) covers quickstarts, gateway and SDK integrations, agent permissions, policy rules, output controls, token budgets, fallback routing, configuration, and operations. Run `pnpm install --frozen-lockfile` and `pnpm dev` from `docs/site` to browse locally. See the [GitHub Pages launch guide](docs/site/src/content/docs/contributing/github-pages.md) to enable the prepared deployment workflow when the project goes live.

## Start the demo

```powershell
uv sync --all-packages
Copy-Item .env.example .env
# Edit .env: set STOPSLOP_KEY and STOPSLOP_JEV_KEY.
uv run --no-sync pytest -q
uv run --no-sync --package stopslop-demo stopslop-demo --scenario nda
```

The shipped example selects NVIDIA chat and Jev classification. The scripted NDA demo uses invented allowed, filtered and blocked examples and verifies the expected actions. Open a second terminal with `uv run --no-sync --package stopslop-top stopslop-top` to inspect violations, chats and quotas. See [demo and submission checklist](docs/demo.md).

For local semantic classification, run `uv sync --all-packages --extra laya` and set `STOPSLOP_CLASSIFIER=laya`. Laya loads on CPU before the demo spinner starts. `--deterministic` explicitly disables semantic checks. Classifier failures block forwarding. Interactive and single-prompt chat never retry; scripted scenarios retry only transient chat HTTP 502/503/504 responses.

## Configure once

Every runtime setting has one name: `STOPSLOP_BASE_URL` in the environment maps to `--base-url` on the gateway and demo, and `base_url` in Python. Precedence is **explicit flag > process environment > selected .env file > built-in default**. Reading a dotenv file does not change the process environment. Use `--env-file PATH` for a different file.

| Setting | Flag | Purpose |
| --- | --- | --- |
| `STOPSLOP_PROVIDER` | `--provider` | `nvidia`, `openai`, or `ollama` |
| `STOPSLOP_BASE_URL` | `--base-url` | Upstream OpenAI-compatible `/v1` endpoint |
| `STOPSLOP_MODEL` | `--model` | Upstream model name |
| `STOPSLOP_KEY` | `--key` | Upstream credential; prefer the environment |
| `STOPSLOP_CLASSIFIER` | `--classifier` | `jev`, `laya`, or `llm` |
| `STOPSLOP_FALLBACK_CLASSIFIER` | `--fallback-classifier` | Optional independent budget assessment backend |
| `STOPSLOP_POLICY_FILE` | `--policy-file` | Enforcement policy; discovers `policy.toml` in the current directory |
| `STOPSLOP_STATE_FILE` | `--state-file` | Shared SQLite state and reporting |
| `STOPSLOP_LOG_CHATS` | `--log-chats` / `--no-log-chats` | Record original inputs and delivered replies |

Provider-specific environment variables and `MAIN_*` settings are removed. Set the four upstream values together when switching providers; explicit endpoint/model values always apply to the selected provider. NVIDIA and OpenAI have built-in endpoint/model defaults. Ollama requires the exact name of an installed model and supplies a dummy key when the key is empty. Approve your chosen model in `policy.toml` and adjust model-scoped budgets.

Policy files contain enforcement decisions: rules, thresholds, actions, model approvals, budgets, output checks and permissions. They contain no classifier or provider settings. Classifier selection is fixed for a running process, while policy decisions reload before each request. `jev` requires `STOPSLOP_JEV_KEY`; `llm` uses the four upstream settings. Local privacy routes use `STOPSLOP_LOCAL_BASE_URL`, `STOPSLOP_LOCAL_MODEL`, `STOPSLOP_LOCAL_KEY`; trusted hosted fallback routes use the corresponding `STOPSLOP_FALLBACK_*` settings.

## Packages and commands

| Package | Responsibility | Command |
| --- | --- | --- |
| `stopslop` | Policy engine, SDK transport, persistence and administration | `stopslop-policy` |
| `stopslop-proxy` | HTTP gateway for OpenAI, NVIDIA and Ollama | `stopslop-proxy` |
| `stopslop-demo` | Interactive chat and verified scripted scenarios | `stopslop-demo` |
| `stopslop-top` | Read-only terminal dashboard | `stopslop-top` |

The core package has no FastAPI, Uvicorn or terminal UI dependency. Install all workspace packages for `just test`, `just demo`, `just proxy`, `just top`, and `just clean`. `just demo` respects your `.env`. `just demo-local --model llama3.2` selects local Ollama and Laya; install Laya first. Ollama must already serve that model. Neither recipe installs models or starts Ollama.

## Gateway and SDK

```powershell
uv run --no-sync --package stopslop-proxy stopslop-proxy
# Or override the same settings for this run:
uv run --no-sync --package stopslop-proxy stopslop-proxy --provider openai --base-url https://api.openai.com/v1 --model gpt-4.1-mini
uv run --no-sync --package stopslop-proxy stopslop-proxy --provider ollama --base-url http://127.0.0.1:11434/v1 --model llama3.2 --key ollama
```

Set `STOPSLOP_KEY` for the selected hosted provider. Gateway client tokens are separate from upstream credentials. The gateway listens on `http://127.0.0.1:8000` by default; `--host` and `--port` change the listener.

In your client environment, install the `openai` package and use text Chat Completions:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="YOUR_GATEWAY_CLIENT_TOKEN",
    max_retries=0,
)
response = client.chat.completions.create(
    model="llama3.2",
    messages=[{"role": "user", "content": "Explain how rain forms."}],
    max_tokens=96,
    stream=False,
)
print(response.choices[0].message.content)
```

For an unauthenticated loopback gateway, use any nonempty client key. The configured upstream model replaces the client's model unless `--preserve-model` is enabled; policy approvals still apply. Responses, native Ollama `/api/chat`, streaming, tool calls and multimodal content are unsupported.

For synchronous SDK clients without a gateway process:

```python
import httpx
from openai import OpenAI
from stopslop.config import Settings
from stopslop.router import PolicyRouter

settings = Settings.load()
with OpenAI(
    base_url=settings.base_url,
    api_key=settings.client_token or settings.key,
    max_retries=0,
    http_client=httpx.Client(transport=PolicyRouter(settings), timeout=settings.timeout),
) as client:
    response = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "user", "content": "Explain how rain forms."}],
        max_tokens=96,
    )
    print(response.choices[0].message.content)
```

The demo uses this same transport. For asynchronous SDK clients, use the HTTP gateway. Library integrations can build a gateway with `from stopslop_proxy import create_app`.

## Edit policy

`policy.toml` is the editable policy catalog. It includes privacy controls, historical attack signatures, semantic thresholds, approved models, output checks, a daily token budget, and example tool/MCP/memory permissions. Add installed local model names to `allowed_models` before using them.

```toml
version = 1
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

`STOPSLOP_STATE_FILE` / `--state-file` selects one SQLite database, default `stopslop.sqlite3`. Use the same absolute path for the gateway, SDK/demo, monitor and administration. It contains quota consumption, pending reservations, suspensions, sessions, audit records, all violations, incident metadata, generated policies and recorded chats. No runtime JSON snapshots, log files, incident feeds or session directories are created. SQLite may use a transient rollback journal during transactions.

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
# route = "fallback" # Cheaper/free provider: STOPSLOP_FALLBACK_* settings
```

The shipped policy enables local fallback for the main model's daily budget. Configure `STOPSLOP_LOCAL_MODEL` to an installed model and add that exact name to `allowed_models`. For a provider fallback, configure its URL, key and model through `STOPSLOP_FALLBACK_BASE_URL`, `STOPSLOP_FALLBACK_KEY` and `STOPSLOP_FALLBACK_MODEL`, and change the route to `"fallback"`. Provider prices are an operator choice; StopSlop does not infer which model is cheaper or free.

Admission tries the alternate destination once before making a chat-provider call. It preserves redactions, model approvals, authentication and output checks. Every configured budget is checked again for the new model: an overall budget without `models` remains a hard cap, and a fallback model's own quota can still reject the request. A local-only privacy rule cannot switch to a cloud provider. Missing/unapproved destinations fail closed; provider errors do not trigger a budget fallback or retries.

If a hosted classifier uses the exhausted budget, set `STOPSLOP_FALLBACK_CLASSIFIER=laya` (or `--fallback-classifier laya`) to keep semantic input/output checks active. Classifier errors remain blocking. Response headers `X-StopSlop-Budget-Fallback` and `X-StopSlop-Model`, audit records, metrics and chat logs identify the fallback destination. `X-StopSlop-Action` continues to describe the input privacy action.

## Browse the TUI

Run `stopslop-top --state-file PATH` in another terminal:

- **Overview**: sessions, controls, quota usage and telemetry; scroll to see the full dashboard.
- **Violations**: timestamped history, newest first, with no 1,000-record browsing cap.
- **Chats**: recorded requests and delivered replies. Select a row and press Enter to read it.

Tab or 1/2/3 switches views. Arrows, Page Up/Down and Home/End navigate. Enter opens details; Escape returns; Q exits. Details scroll independently. Timestamps include the local UTC offset. `--once` or redirected output prints a dashboard snapshot. The monitor opens SQLite read-only.

Chat recording is **on by default**. Disable new recordings with `--no-log-chats` on the gateway or demo, or set `STOPSLOP_LOG_CHATS=false`. `--log-chats` explicitly enables it again. This stores original input messages, delivered replies, timestamps, model, action and client identity. Blocked attempts have a status and no delivered reply. Headers, provider keys and anonymous replacement dictionaries are not recorded. Original inputs can contain sensitive data; treat the database accordingly. Turning logging off stops new content records but does not remove existing ones.

## Gateway and agent permissions

```powershell
uv run --no-sync --package stopslop-proxy stopslop-proxy --policy-file policy.toml --preserve-model
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

Stop the gateway, demo and monitor, then run `just clean` to reset local state. This deletes workspace SQLite databases and their journals/WAL files, legacy telemetry, incident feeds, session snapshots and generated `policy.dyn.*` exports. It preserves `.env`, `policy.toml`, source code and dependencies. Set `STOPSLOP_STATE_FILE` in `.env` or the environment to identify a custom database inside the workspace. Migration from older state formats is not supported.

Orphan release refuses live or unknown owner processes. Check that remote provider work has also stopped before releasing a reservation. See [governance](docs/governance.md) for implementation limits and [requirements](docs/requirements.md) for competition scope.
