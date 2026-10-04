---
title: Python SDK transport
description: Enforce policy inside a synchronous OpenAI client with PolicyRouter.
sidebar:
  order: 2
---

`PolicyRouter` is a synchronous `httpx.BaseTransport`. It intercepts text Chat Completions and forwards the prepared request through the same policy engine as the gateway.

## Create a scoped client

Install the core workspace package and the OpenAI SDK in the environment running your application:

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
    http_client=httpx.Client(
        transport=PolicyRouter(settings),
        timeout=settings.timeout,
    ),
) as client:
    response = client.chat.completions.create(
        model=settings.model,
        messages=[{"role": "user", "content": "Explain how rain forms."}],
        max_tokens=96,
        stream=False,
    )
    print(response.choices[0].message.content)
```

When access tokens are configured, `settings.client_token` must be one of the assigned client tokens. The router replaces the client Authorization header with the selected upstream key after admission.

Use the context manager to close the client, transport, and owned runtime resources. Keep one client for a sequence of requests instead of constructing one for every message.

## Override settings for one integration

```python
settings = Settings.load(
    env_file=".env.service",
    provider="ollama",
    base_url="http://127.0.0.1:11434/v1",
    model="llama3.2",
    key="ollama",
    classifier="laya",
    policy_file="policy.toml",
    state_file="/absolute/path/stopslop.sqlite3",
    log_chats=False,
)
```

The model must be installed and approved. Laya requires the optional dependency. On Windows use an absolute Windows path for `state_file`. Explicit values override process environment and dotenv values.

## Multi-turn conversations

Maintain history in your application and submit the full text conversation on each turn. Only message objects containing `role` and string `content` are accepted. StopSlop checks every submitted message; previously delivered assistant text is not exempt on later requests. History increases both inspection work and quota reservations.

## Injection boundaries

`PolicyRouter(settings, upstream=..., evaluator=..., repository=...)` allows controlled replacement of provider transport, semantic assessment, and persistence. An injected repository must implement the full `Repository` protocol and atomic transactions that serialize admission across users. See [storage](../../operations/storage/).

Requests outside POST paths ending in `/chat/completions` receive `unsupported_endpoint`. Transport-level upstream failures can surface as Python exceptions; the HTTP gateway maps upstream connection failures to a JSON `upstream_unavailable` error. Use the gateway for asynchronous SDK clients.
