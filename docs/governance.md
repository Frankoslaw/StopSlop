# Output enforcement and adaptive policies

## Setup

The shipped policy selects local Laya. Install and keep its optional dependencies:

```powershell
uv sync --all-packages --extra laya
uv run --no-sync pytest -q
```

The gateway and demo report a missing semantic dependency before accepting requests.
To use the existing demo chat provider for assessment instead, change the policy's
`classifier` to `llm`. `jev` requires its separate key. There is no automatic backend
fallback. `--deterministic` deliberately disables semantic input and output rules.
Tests mock classifiers; their passing results do not establish real model accuracy.

## Output rules

Every response is checked before delivery, including content, reasoning text, and
refusals in every choice. Tool calls, nontext content, and malformed responses are
rejected. Input rules apply to output by default: `block` becomes an output block,
`filter` redacts newly generated matches, `allow` leaves matches unchanged, and
`local`/`redirect` become blocks because an existing response cannot be rerouted.
Per-rule exemptions remain scoped to their rule. Checks happen before exact anonymous
tokens are restored, so legitimate round trips can restore a caller's own data.

Add an optional `output` object to a version 1 policy:

```json
{
  "inherit_input": true,
  "default_action": "block",
  "rules": [
    {"id": "secret", "action": "block_device"},
    {"id": "profanity", "action": "warn"},
    {"id": "unsafe_reply", "pattern": "execute_remote_payload", "action": "block"},
    {"id": "output_exfiltration", "description": "Block instructions to transmit private organizational data to an unauthorized recipient.", "threshold": 85, "action": "block_device"}
  ]
}
```

An existing input ID can be overridden without repeating its definition. New rules
use `pattern` or `description`. Supported output actions are `block`, `block_device`,
`warn`, `filter`, and `allow`; mixed matches use that precedence, with `allow` last.
`default_action` sets the output consequence for inherited block/local rules and
explicit rules without an action. `inherit_input: false` explicitly restricts output
checks to the listed rules. `warn` records the violation and delivers the reply;
`block` withholds it with HTTP 403 and `output_blocked`. Output evaluator failures
withhold the reply. Already generated chat tokens remain charged even when a reply
is blocked or assessment fails. Successful replies expose `X-StopSlop-Output-Action`,
`X-StopSlop-Output-Rules`, and `X-StopSlop-Output-Risks`.

## Authenticated client suspension

Set `STOPSLOP_ACCESS_TOKENS` to a JSON object mapping stable client IDs to unique
bearer tokens, supplied from the ignored `.env` or an external secret store. Clients
send `Authorization: Bearer <client-token>`. Caller-provided device IDs and IP addresses
do not determine identity. The upstream receives its configured provider key, never
the client token. The demo uses `STOPSLOP_CLIENT_TOKEN` when set.

`block_device` withholds the reply and suspends that authenticated client. Further
calls return `device_blocked`; other clients can continue. Suspension persists when
a shared state file is configured. Without authenticated identity, this action blocks
the reply but cannot suspend a device. This is gateway client suspension, not operating
system or firewall quarantine. Administrative reset is a local command:

```powershell
uv run --no-sync --package stopslop stopslop-policy reset-client laptop-1 --state-file stopslop-state.sqlite3
```

The gateway defaults to localhost and refuses a non-loopback bind without configured
client tokens. For remote use, also provide transport encryption through your normal
TLS deployment. There is no unauthenticated HTTP reset endpoint.

## Model and quota governance

An optional `allowed_models` array lists approved chat model names, including any
local/fallback destinations. With `--preserve-model`, arbitrary caller-selected models
are rejected unless this list permits them; without a list, only the configured main
model is permitted on the main route. Without `--preserve-model`, the main model is
pinned as before. Budget `models` selectors only scope accounting and do not grant
model permission. The shipped policy includes an unscoped daily total-token budget
so model selectors cannot leave another model outside that overall cap.

The gateway defaults to `stopslop-state.sqlite3`. Configure the same absolute
`STOPSLOP_STATE_FILE` across gateway workers, SDK/demo processes, and administrative
generation to share quota admission and client suspensions on one host. SQLite
transactions make admission atomic across processes. Completed usage is timestamped
at completion. Pending reservations count in every window until finished, including
across fixed resets and rolling-window expiry. A restart does not reset quotas.

Semantic assessments reserve budget under their classifier model name before calling
the classifier. Their token counts are conservative estimates of conversation and
question overhead plus structured output, not provider-reported billing. Chat and
generation calls reconcile estimates with provider usage when available. This is token
governance, not currency pricing or a GPU-compute limiter. Errors without usage release
the reservation; providers may charge for work they do not report. SQLite state is
intended for a shared local disk, not distributed hosts or a network filesystem.

Crash reservations remain charged conservatively. Inspect and release one only after
its owner process has exited:

```powershell
uv run --no-sync --package stopslop stopslop-policy reservations --state-file stopslop-state.sqlite3
uv run --no-sync --package stopslop stopslop-policy release-orphan RESERVATION_ID --state-file stopslop-state.sqlite3
```

Release refuses reservations owned by a live or unknown process. An exited gateway
does not prove that a remote provider has stopped processing: verify that work has
ended before invoking release. PID reuse can conservatively prevent release.

## Live reload and historical attack controls

Base policy and optional complementary policy files are validated on change and
reloaded for the next request. A malformed update fails new requests closed; it does
not silently remove protections. In-flight output checks keep their admitted policy
snapshot. Use atomic file replacement when editing feeds. Legacy `--rules-file`
configuration still loads at startup.

The shipped policy demonstrates signatures for instruction override, unsafe
`pickle`/`dill` loading, explicitly unsafe `torch.load`, and shell-execution constructs,
plus a semantic execution/exfiltration rule. These are deliberately restrictive
textual controls: even quoted unsafe code can match a signature. They do not execute
or remediate code, authenticate MCP calls, inspect model repositories, or prove that a
device executed a command. External agents must report actual execution or leak events.

These examples address risks described by [OWASP's prompt injection guidance](https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html),
[OWASP's output-handling guidance](https://owasp.github.io/www-project-top-10-for-large-language-model-applications/2_0_vulns/LLM05_ImproperOutputHandling),
and [PyTorch's security policy](https://github.com/pytorch/pytorch/security/policy).
Their tests verify specific signatures and benign descriptions, not comprehensive
coverage of every historical exploit.

## Generate `policy.dyn.json`

Enable automatic metadata recording and optional complementary enforcement:

```powershell
uv run --no-sync --package stopslop stopslop --policy-file policy.json --incident-file incidents.jsonl --dynamic-policy-file policy.dyn.json
```

The dynamic file may initially be absent. Input blocks and output violations append
incident metadata without prompts, outputs, credentials, or anonymous mappings.
External agents/operators can report confirmed incidents:

```powershell
uv run --no-sync --package stopslop stopslop-policy record --kind code_execution --rule shell_execution --incidents incidents.jsonl
uv run --no-sync --package stopslop stopslop-policy record --kind data_leak --rule email --incidents incidents.jsonl
uv run --no-sync --package stopslop stopslop-policy generate --incidents incidents.jsonl --policy-file policy.json --output policy.dyn.json
```

Generation is explicit and makes one bounded, non-retried call to the same chat
provider/model configured for the demo. It sends the latest 100 incident metadata
records plus the base and existing dynamic policies, not raw incident content.
It shares quotas when configured with the gateway's state file. There are no automatic
learning calls on the request path. To repeat generation on a schedule, invoke this
command through your administrative scheduler.

The model may propose deterministic literals or semantic descriptions. Literal
signatures are escaped locally; unrestricted generated regexes are rejected. Only new
`dyn_` IDs and restrictive actions are accepted. Generated input rules cannot override
base/built-in IDs, add exceptions, change models/budgets/classifiers, or disable inherited
output protection. Output additions can block replies or suspend authenticated clients.
Invalid generations and provider errors preserve the previous file. Writers are
serialized, and successful updates replace the file atomically while retaining prior
dynamic rules. Loading a configured dynamic file activates its validated rules on the
next request. Without `--dynamic-policy-file`, generation produces a separate artifact
for inspection only. Model-generated semantic rules can still be overbroad or inaccurate;
validate them with your own allowed/blocked examples before enabling a generated feed.

## Reporting

The terminal monitor shows active controls, thresholds, shared quota scopes, recent
input/output decisions, tokens, failures, and latency. Snapshots retain the latest
1,000 violations, and audit files rotate at 5 MiB with five backups. Decision records
include direction, action, client identity where known, rules, semantic risk, and
model where available. Request-completion records include usage and latency. No
conversation content or credentials are logged. Use a separate audit path per worker
or a central logging collector for heavy multiworker deployments; the built-in file
rotation is local to each process. Financial costs and semantic accuracy benchmarks
are not inferred from these metrics.
