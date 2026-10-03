# StopSlop

A hybrid AI control layer: deterministic privacy/security rules, semantic checks, output filtering, token quotas, and authenticated agent permissions. Integrate through an OpenAI-compatible HTTP gateway or an `httpx` SDK transport. Python 3.14 is required.

## Run

```powershell
uv sync --all-packages --extra laya
uv run --no-sync pytest -q
uv run --no-sync --package stopslop_demo stopslop-demo --scenario nda --policy-file policy.toml
uv run --no-sync --package stopslop_top stopslop-top
```

Copy `.env.example` to `.env` and set `STOPSLOP_MAIN_KEY`. Laya runs locally on CPU. The demo downloads and loads classifier weights before starting its request spinner, including a configured Laya budget fallback; later turns check for policy changes before starting progress. Download failures stop the demo before any chat-provider request. `classifier = "jev"` requires its own key; `classifier = "llm"` uses the configured chat provider. `--deterministic` explicitly disables semantic rules. Classifier failures block forwarding; backends never silently substitute for one another.

The demo checks each turn and its generated reply. `--scenario nda` runs invented allowed, filtered and blocked examples. Interactive and single-prompt chat never retry; scenario retries only transient chat HTTP 502/503/504 responses. Tests use mock providers and classifiers, with no API calls or credentials.

## Tasks and workspace packages

Install [just](https://just.systems/) and run `just test`, `just demo`, `just top`, or `just clean`. Set `UV` to override the uv executable. The monitor lives in the separate `packages/stopslop_top` workspace package; the proxy package contains no terminal UI or Rich dependency. Install all workspace packages before running these recipes. The legacy `slopstop-top` command remains an alias supplied by the monitor package.

### Hosted and local demos

`just demo` selects Jev classification and the NVIDIA Build chat API. Fill in `STOPSLOP_JEV_KEY` and `STOPSLOP_NVIDIA_KEY` in `.env`; the NVIDIA URL/model defaults are in `.env.example`.

`just demo-local` selects Laya classification and Ollama chat. Fill in `STOPSLOP_OLLAMA_BASE_URL`, `STOPSLOP_OLLAMA_MODEL` and `STOPSLOP_OLLAMA_KEY` for your connected Ollama endpoint. Laya is prepared before the spinner starts. Ollama must already serve the selected model; the demo does not install or start Ollama.

Both recipes accept extra flags, including a model override:

```powershell
just demo
just demo-local --model llama3.2
just demo-local --model your-installed-model --main-base-url http://your-ollama-host:11434/v1
```

`--classifier` overrides both the policy's main classifier and its configured budget fallback classifier for this run. This lets the hosted demo use Jev throughout without loading Laya. Provider flags choose the corresponding `STOPSLOP_NVIDIA_*` or `STOPSLOP_OLLAMA_*` settings. Explicit URL/model flags take precedence, followed by provider-specific environment settings, generic `STOPSLOP_MAIN_*` settings, then defaults. Keys stay in the environment. Approve your chosen chat model in `policy.toml`'s `allowed_models`.

## OpenAI SDK, NVIDIA and Ollama

Select the upstream with `--main-provider` or `STOPSLOP_MAIN_PROVIDER` (`nvidia`, `openai`, `ollama`). NVIDIA remains the default. All three use the same policy checks and OpenAI-compatible text Chat Completions endpoint. `STOPSLOP_MAIN_BASE_URL`, `STOPSLOP_MAIN_MODEL` and `STOPSLOP_MAIN_KEY` override provider defaults when provider-specific settings are absent; remove any explicit NVIDIA URL/model entries from an existing `.env` when switching providers. Gateway client tokens and upstream provider keys are separate credentials.

Run one of these PowerShell examples:

**NVIDIA API** (the existing backend):

```powershell
$env:STOPSLOP_MAIN_KEY = "YOUR_NVIDIA_API_KEY"
uv run --no-sync --package stopslop stopslop --main-provider nvidia --policy-file policy.toml
```

**OpenAI API**, using an approved Chat Completions model:

```powershell
$env:STOPSLOP_MAIN_KEY = "YOUR_OPENAI_API_KEY"
uv run --no-sync --package stopslop stopslop --main-provider openai --main-base-url https://api.openai.com/v1 --main-model gpt-4.1-mini --policy-file policy.toml
```

**Local Ollama**: start Ollama and pull the model first. The installed model name must match exactly; the provider supplies a dummy key if no main key is set.

```powershell
ollama pull llama3.2
$env:STOPSLOP_MAIN_KEY = "ollama"
uv run --no-sync --package stopslop stopslop --main-provider ollama --main-base-url http://127.0.0.1:11434/v1 --main-model llama3.2 --policy-file policy.toml
```

Install the Laya extra for these policy examples. Add the selected model to `policy.toml`'s `allowed_models`, and adjust model-scoped budgets for it. Ollama as the main provider is separate from `STOPSLOP_LOCAL_*`, which configures privacy/budget fallback routing. Hosted semantic checks with `classifier = "llm"` use the selected main provider; Laya keeps semantic classification local.

Install the `openai` Python package in your client environment (`uv add openai`; the workspace demo already depends on it). The client pattern is identical for every upstream:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key="YOUR_GATEWAY_CLIENT_TOKEN",  # Any nonempty value for an unauthenticated loopback gateway.
    max_retries=0,
)
response = client.chat.completions.create(
    model="llama3.2",  # Use your selected upstream model.
    messages=[{"role": "user", "content": "Explain how rain forms."}],
    max_tokens=96,
    stream=False,
)
print(response.choices[0].message.content)
```

By default the gateway replaces the client's model with its configured main model. Use `--preserve-model` to honor client model selection within the policy allowlist. Use `chat.completions.create`; Responses, native Ollama `/api/chat`, streaming, tool calls and multimodal content are unsupported. See the [OpenAI SDK](https://github.com/openai/openai-python) and [Ollama OpenAI compatibility documentation](https://docs.ollama.com/api/openai-compatibility).

### Direct SDK transport (no gateway process)

The synchronous OpenAI SDK can also enforce policies locally through `PolicyRouter`. This works with each provider above; configure the same `STOPSLOP_MAIN_*` environment variables and policy model allowlist:

```python
import httpx
from openai import OpenAI
from stopslop.config import Settings
from stopslop.router import PolicyRouter

settings = Settings.load(policy_file="policy.toml")
with OpenAI(
    base_url=settings.main_base_url,
    api_key=settings.client_token or settings.main_key,
    max_retries=0,
    http_client=httpx.Client(transport=PolicyRouter(settings), timeout=settings.timeout),
) as client:
    response = client.chat.completions.create(
        model=settings.main_model,
        messages=[{"role": "user", "content": "Explain how rain forms."}],
        max_tokens=96,
    )
    print(response.choices[0].message.content)
```

For an asynchronous SDK client, use the HTTP gateway example. The demo uses the synchronous transport and accepts the same provider selection:

```powershell
uv run --no-sync --package stopslop_demo stopslop-demo --main-provider openai --main-base-url https://api.openai.com/v1 --model gpt-4.1-mini --policy-file policy.toml "Explain how rain forms."
uv run --no-sync --package stopslop_demo stopslop-demo --main-provider ollama --main-base-url http://127.0.0.1:11434/v1 --model llama3.2 --policy-file policy.toml "Explain how rain forms."
```

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
classifier = "laya"   # Optional classifier for exhausted paid semantic assessments
# route = "fallback" # Cheaper/free provider: STOPSLOP_FALLBACK_* settings
```

The shipped policy enables local fallback for the main model's daily budget. Configure `STOPSLOP_LOCAL_MODEL` to an installed model and add that exact name to `allowed_models`. For a provider fallback, configure its URL, key and model through `STOPSLOP_FALLBACK_BASE_URL`, `STOPSLOP_FALLBACK_KEY` and `STOPSLOP_FALLBACK_MODEL`, and change the route to `"fallback"`. Provider prices are an operator choice; StopSlop does not infer which model is cheaper or free.

Admission tries the alternate destination once before making a chat-provider call. It preserves redactions, model approvals, authentication and output checks. Every configured budget is checked again for the new model: an overall budget without `models` remains a hard cap, and a fallback model's own quota can still reject the request. A local-only privacy rule cannot switch to a cloud provider. Missing/unapproved destinations fail closed; provider errors do not trigger a budget fallback or retries.

If a hosted classifier uses the exhausted budget, configure an available fallback classifier (usually local Laya) to keep semantic input/output checks active. Classifier errors remain blocking. Response headers `X-StopSlop-Budget-Fallback` and `X-StopSlop-Model`, audit records, metrics and chat logs identify the fallback destination. `X-StopSlop-Action` continues to describe the input privacy action.

## Browse the TUI

Run `stopslop-top --state-file PATH` in another terminal:

- **Overview**: sessions, controls, quota usage and telemetry; scroll to see the full dashboard.
- **Violations**: timestamped history, newest first, with no 1,000-record browsing cap.
- **Chats**: recorded requests and delivered replies. Select a row and press Enter to read it.

Tab or 1/2/3 switches views. Arrows, Page Up/Down and Home/End navigate. Enter opens details; Escape returns; Q exits. Details scroll independently. Timestamps include the local UTC offset. `--once` or redirected output prints a dashboard snapshot. The monitor opens SQLite read-only.

Chat recording is **on by default**. Disable new recordings with `--no-log-chats` on the gateway or demo, or set `STOPSLOP_LOG_CHATS=false`. `--log-chats` explicitly enables it again. This stores original input messages, delivered replies, timestamps, model, action and client identity. Blocked attempts have a status and no delivered reply. Headers, provider keys and anonymous replacement dictionaries are not recorded. Original inputs can contain sensitive data; treat the database accordingly. Turning logging off stops new content records but does not remove existing ones.

## Gateway and agent permissions

```powershell
uv run --no-sync --package stopslop stopslop --policy-file policy.toml --preserve-model
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
