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
| filter | Redact matched spans, use main model |
| redirect | Send original content to explicitly trusted fallback |
| passthrough | Send original content to main model |

CLI overrides environment, then `.env`, then defaults. Configure fallback URL/model/key
with `STOPSLOP_FALLBACK_*`. Rules load at startup from `--rules-file` (server) or
`STOPSLOP_RULES_FILE`; example rules: `packages/stopslop/src/stopslop/default_rules.json`.
Names use a small dictionary; PESEL/bank matches require checksums. NDA inference,
word-number decoding, response scanning, streaming and tools are outside this version.

Optional server: `uv run --package stopslop stopslop --policy block --port 8000`.
Clients can use `http://127.0.0.1:8000/v1`. Only routed text chat is inspected.
The server shares the demo's policy engine; it is intended for localhost development.
See [requirements](docs/requirements.md) for the brief competition scope.
