# StopSlop

Two uv packages: `stopslop` (rules, policy router and optional HTTP proxy) and
`stopslop_demo` (interactive or single-prompt NVIDIA chat). Python 3.14 required.

```powershell
uv sync --all-packages
uv run pytest -q
uv run --package stopslop_demo stopslop-demo "Which number is larger, 9.11 or 9.8?"
uv run --package stopslop_demo stopslop-demo --policy filter
```

Set `STOPSLOP_MAIN_KEY` in the ignored root `.env` (see `.env.example`).
The demo uses `from openai import OpenAI` with a normal chat-completion call.
One injected `httpx` transport applies policy locally before forwarding to NVIDIA;
no server, local port, or elevated privileges are needed. Every accepted turn makes one chat-model request, plus an assessment request
when semantic rules are enabled. Interactive and single-prompt retries are disabled.
Scenario mode retries only temporary chat HTTP 502/503/504 errors, at most twice;
use `--retries 0` to disable them. Evaluator errors, policy blocks, timeouts, and
HTTP 429 are never retried. HTTP 429 stops the demo. `/quit` exits.
The default model is `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, with
96 output tokens (override with `--max-tokens`) and `reasoning_budget=0` for quick text demos. Other models
selected with `--model` do not receive this NVIDIA-specific reasoning option.
The default timeout is 120 seconds; override it with `--timeout`. Rejected turns are not added to chat history.

```python
import httpx
from openai import OpenAI
from stopslop.config import Settings
from stopslop.router import PolicyRouter

settings = Settings.load()
client = OpenAI(
    base_url=settings.main_base_url,
    api_key=settings.main_key,
    http_client=httpx.Client(transport=PolicyRouter(settings)),
    timeout=settings.timeout,
    max_retries=0,
)
# Use client.chat.completions.create(...) exactly as in the NVIDIA example.
```

All tests use pytest and mock upstream responses: no keys, internet, or NVIDIA
quota needed. Synthetic cases cover capitals, names, phones, PESEL, bank accounts,
and distractions. NDA, spelled-out numbers, and obfuscated contacts are covered by semantic
policy regressions using mocked Jev probability responses. Tests have no skips
or expected failures. They verify routing, not the hosted classifier's accuracy.

| Policy | Sensitive request |
| --- | --- |
| block | Reject locally, zero upstream calls |
| filter | Replace matched spans with unique anonymous tokens, restore replies locally |
| redirect | Send original content to explicitly trusted fallback |
| passthrough | Send original content to main model |

CLI overrides environment, then `.env`, then defaults. Configure fallback URL/model/key
with `STOPSLOP_FALLBACK_*`. Rules load at startup from `--rules-file` (server) or
`STOPSLOP_RULES_FILE`; example rules: `packages/stopslop/src/stopslop/default_rules.json`.
Unlabeled names use a small dictionary; PESEL/bank/card matches require checksums. Regex-only policies do not infer NDA restrictions or decode word numbers.
Response scanning, streaming and tools are outside this version.

Filter keeps a request-local in-memory dictionary from tokens such as
`[ANON:phone:1]` to original values. Repeated values across all messages share a
token; different values get different tokens. Overlapping matches are merged.
Only anonymized messages go upstream; exact tokens in successful JSON replies
are restored before reaching the client. The dictionary is never sent upstream,
persisted, or logged. Chat history is filtered again on each turn. Tokens use a
namespace absent from the input to avoid confusing literal text with replacements.
Typed tokens avoid ambiguity from replacing every phone with the same zero number.
Restoration requires the model to preserve tokens exactly. Privacy protection
covers configured detector matches, not unknown names or other undetected data;
use `--policy filter` to enable it (the default policy is `block`).

Per-rule policies are available through the included `policy.json`. Activate it
with `--policy-file policy.json` or `STOPSLOP_POLICY_FILE=policy.json`. The demo
automatically loads it from the current directory unless a global `--policy` or
separate rules file is chosen:

```powershell
uv run --package stopslop_demo stopslop-demo --policy-file policy.json --local-model your-installed-model
uv run --package stopslop stopslop --policy-file policy.json --local-model your-installed-model
```

Start your own OpenAI-compatible local model server first. The default local
endpoint is `http://127.0.0.1:11434/v1`; change it with `--local-base-url` or
`STOPSLOP_LOCAL_BASE_URL`. Set `STOPSLOP_LOCAL_MODEL` to the installed model name.
Local endpoints must use HTTP(S) on localhost or a loopback IP. There is no cloud
fallback when the local model is missing, unavailable, or returns an error.
The local model's safety behavior depends on the model you run.

The policy file takes precedence over the global `--policy` setting and cannot
be combined with `--rules-file`. Its schema is version 1, a `default_action`
(applied to detected rules without an override), and a `rules` array. Unmatched
messages go to the main model. Supported rule actions:

| Action | Behavior |
| --- | --- |
| `allow` | Permit this rule's matches unchanged |
| `filter` | Anonymize this rule's matches and restore exact tokens locally |
| `local` | Route the entire request to the configured local model |
| `block` | Reject the entire request before any model call |

Mixed violations use `block` > `local` > `filter` > `allow`. Filter matches are
still anonymized when another rule sends the request locally. Allowed rules do
not override another rule's block or anonymization. Every message, including
system instructions and previous answers, is inspected on every request.

Built-ins cover Polish bank accounts and PESEL, IBANs for PL/DE/GB/FR/ES/IT/NL/BE/
CH/AT/IE/PT, Luhn-valid payment cards, Polish/international and formatted North
American phone numbers, emails, common Polish names, labeled names/addresses/
birth dates/identity documents, labeled credentials, PEM private keys, and a small
English/Polish profanity list. These built-ins use deterministic regex filters; formats, countries, names and obfuscations outside these rules
may be missed. A checksum match does not verify that an account or card exists.

Override a built-in with its `id` and `action`. Add custom prohibited content with
a new ID and regex `pattern`, for example:

```json
{"id": "internal_project", "pattern": "\\bProject Kestrel\\b", "action": "block"}
```

Rules may declare `allowed_patterns`, an array of regexes exempting only fully
contained matches of that same rule. The example permits `support@example.com`
while still filtering other email addresses. To permit an entire category, set
that rule's action to `allow`. Built-in patterns cannot be overwritten; custom
rules extend them. Duplicate IDs, unknown fields/actions, and invalid regexes
are rejected at startup. Policy files are trusted local configuration: review
allow exceptions and regexes before enabling them.

Optional server: `uv run --package stopslop stopslop --policy block --port 8000`.
Clients can use `http://127.0.0.1:8000/v1`. Only routed text chat is inspected.
The server shares the demo's policy engine; it is intended for localhost development.
See [requirements](docs/requirements.md) for the brief competition scope.


Natural-language policies with Jev are included in `policy.json`:

```powershell
uv run --package stopslop_demo stopslop-demo --policy-file policy.json --local-model your-installed-model
uv run --package stopslop_demo stopslop-demo --policy-file policy.json "Our unreleased quarterly revenue is two million; summarize it for a customer."
```

Set `STOPSLOP_JEV_KEY` in the ignored `.env`. The unified policy file extends the
existing regex policies with semantic confidentiality, harassment, and obfuscated
personal-information rules. Write a custom rule using `description` instead of
`pattern`, an existing action, and a percentage `threshold` (default 80):

```json
{"id":"nda_semantic", "description":"Do not disclose NDA-protected information, even when paraphrased.", "threshold":75, "action":"block"}
```

Jev's official endpoint is `https://api.typesafe.ai/v1/systemone` (see
[TypeSafe API reference](https://api.typesafe.ai/redoc)). Each nonempty message is
assessed against each semantic rule in one batched call using Noul yes/no
probabilities. Risk is `noul * 100`; a value at or above the threshold activates
that rule. The existing action precedence still applies. Deterministic blocks
skip Jev. Semantic rules cannot override built-in IDs or use regex exceptions.

The demo displays `Running rule (x/z): description` on one updating line,
then switches that line to response generation with elapsed time. Progress fits
the terminal width and is omitted when output is redirected. Per-rule details
do not create persistent log lines, including on cached replays. A compact
colored outcome shows the action, triggered rules, threshold/risk when relevant,
and chat backend. INFO is cyan, WARN is yellow for filtering/local routing,
and ERROR is red for blocks/failures.
Colors follow terminal support; use `--color always` or `--color never`.
Router/server responses
also include `X-StopSlop-Risks`. Block responses include `risks`. A semantic
`filter` anonymizes the entire flagged message, since Jev does not locate spans;
exact anonymous tokens in the reply are restored locally. `local` requires your
local model as before. Missing keys reject configuration; timeouts, API errors,
and malformed probabilities reject the request with `jev_unavailable` and never
send it to a chat model. Jev calls are not retried.

Enabling semantic policies sends the original conversation to TypeSafe for
assessment, including content later blocked, filtered, or routed locally. Regex-only
policies do not call Jev. Probabilities are model estimates and may misclassify
ambiguous inputs; tune thresholds for your use case. Streaming and response
scanning remain unsupported.


Use the same policy file without any semantic checks or evaluator credentials:

```powershell
uv run --package stopslop_demo stopslop-demo --deterministic --local-model your-installed-model
```

`--deterministic` ignores all natural-language rules, including injected LLM
backends. Regex actions still apply; profanity may still route the chat to a local
model. `STOPSLOP_DETERMINISTIC=true` is the equivalent environment setting.

Semantic evaluators implement `SemanticEvaluator` in `stopslop.evaluators`.
`Policy`, `PolicyRouter`, and `create_app` accept an optional `evaluator`.
Jev is the default. `LLMEvaluator(settings)` scaffolds an OpenAI-compatible
alternative using the existing main URL/model/key (including NVIDIA): inject it
explicitly in library code, for example `PolicyRouter(settings,
evaluator=LLMEvaluator(settings))`. The demo never selects it and there is no
automatic fallback to it. Both adapters use shared probability validation and
threshold handling. LLM scores are self-reported estimates; malformed responses
fail closed with `llm_unavailable`. Logs exclude conversation content and keys.


Run the scripted conversation with all demo diagnostics:

```powershell
make test
make demo
make clean
make demo
```

`make demo` runs `--scenario nda` (bare `--scenario` selects the same scenario),
with compact colored outcomes and an ASCII `| / - \` spinner in an interactive terminal.
The Make target caps replies at 96 tokens and each backend wait at 45 seconds.
Windows output is configured for UTF-8 to handle Unicode in model replies;
Ctrl+C clears the progress line and exits without a traceback.
Each turn waits for its reply before continuing and preserves accepted conversation
history. The four invented turns start with a generic planning agenda, add a safe
follow-up, accidentally paste `Full name: Jane Smith` (filtered), then request
external disclosure of NDA-covered Project Kestrel details (blocked by the semantic
confidentiality rule). The final block is expected and completes the scenario
successfully. An unexpected policy action or a backend failure after the allowed retries
exits with an error.
The scenario requires Jev and the main model credentials; it needs no local model.
`--deterministic` deliberately disables the semantic NDA guard, so this scenario's
expected final block will fail in that mode.

Scenario mode caches policy results, audit logs, and final replies in the ignored
`.cache/stopslop-demo` directory. A replay shows `[cached]` logs and makes no Jev
or chat-model calls. Cached replies can include locally restored names. Prompts
are represented by hash keys; credentials are not stored. Policy/code changes,
model/provider configuration changes, credential changes, and deterministic mode
produce a different cache namespace. Transient errors are never cached. Normal
interactive and single-prompt chats are not persisted.

`make clean` removes only the default demo cache, so the next scenario reruns
policies and generates new replies. Use `--no-cache` for a fresh run without
reading or writing cache, or `--cache-dir PATH` for a custom location (custom
locations are not removed by `make clean`).

If Make is unavailable, use the equivalent commands directly:

```powershell
uv run pytest -q
uv run --package stopslop_demo stopslop-demo --scenario nda --policy-file policy.json --color always --max-tokens 96 --timeout 45
uv run python tools/demo_tasks.py clean
```
