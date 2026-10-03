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
no server, local port, or elevated privileges are needed. Every accepted turn makes
one request; automatic retries are disabled. HTTP 429 stops the demo. `/quit` exits.
The default model is `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, with
256 output tokens and `reasoning_budget=0` for quick text demos. Other models
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
and distractions. Unsupported NDA/obfuscation cases are explicit expected failures.

| Policy | Sensitive request |
| --- | --- |
| block | Reject locally, zero upstream calls |
| filter | Replace matched spans with unique anonymous tokens, restore replies locally |
| redirect | Send original content to explicitly trusted fallback |
| passthrough | Send original content to main model |

CLI overrides environment, then `.env`, then defaults. Configure fallback URL/model/key
with `STOPSLOP_FALLBACK_*`. Rules load at startup from `--rules-file` (server) or
`STOPSLOP_RULES_FILE`; example rules: `packages/stopslop/src/stopslop/default_rules.json`.
Unlabeled names use a small dictionary; PESEL/bank/card matches require checksums. NDA inference,
word-number decoding, response scanning, streaming and tools are outside this version.

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
explicitly with `--policy-file policy.json` or `STOPSLOP_POLICY_FILE=policy.json`:

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
English/Polish profanity list. These are deterministic regex filters, not semantic
classification; formats, countries, names and obfuscations outside these rules
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
