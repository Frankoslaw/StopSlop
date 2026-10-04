---
title: HTTP gateway
description: Integrate synchronous, asynchronous, and remote clients through the HTTP gateway.
sidebar:
  order: 1
---

The gateway owns provider credentials, policy enforcement, and quota admission. Clients use its `/v1` URL and their assigned bearer token.

## Run the service

```sh
uv run --no-sync --package stopslop-proxy stopslop-proxy --host 127.0.0.1 --port 8000
```

To expose it beyond loopback, configure client tokens first and terminate HTTPS at the gateway's reverse proxy. See [deployment](../../operations/deployment/).

## Synchronous client

```python
import os
from openai import OpenAI

with OpenAI(
    base_url="http://127.0.0.1:8000/v1",
    api_key=os.environ["STOPSLOP_CLIENT_TOKEN"],
    max_retries=0,
) as client:
    result = client.chat.completions.with_raw_response.create(
        model="your-approved-model",
        messages=[{"role": "user", "content": "Summarize this public article."}],
        max_tokens=128,
        stream=False,
    )
    print(result.headers.get("x-stopslop-action"))
    print(result.headers.get("x-stopslop-model"))
    print(result.parse().choices[0].message.content)
```

`X-StopSlop-Model` identifies the selected model; `X-StopSlop-Budget-Fallback` identifies whether budget routing occurred. Do not infer routing from the privacy action alone.

## Asynchronous client

```python
import asyncio
import os
from openai import AsyncOpenAI

async def main():
    async with AsyncOpenAI(
        base_url="http://127.0.0.1:8000/v1",
        api_key=os.environ["STOPSLOP_CLIENT_TOKEN"],
        max_retries=0,
    ) as client:
        reply = await client.chat.completions.create(
            model="your-approved-model",
            messages=[{"role": "user", "content": "Explain rain."}],
            max_tokens=96,
            stream=False,
        )
        print(reply.choices[0].message.content)

asyncio.run(main())
```

This pattern sends asynchronous requests to the HTTP service; `PolicyRouter` itself is synchronous.

## Direct HTTP

```sh
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_GATEWAY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"your-approved-model","messages":[{"role":"user","content":"Explain rain."}],"max_tokens":96,"stream":false}'
```

The example uses POSIX shell quoting. On Windows use `curl.exe` with appropriate quoting or `Invoke-RestMethod` with a JSON body.

## Embed the application

```python
from stopslop.config import Settings
from stopslop_proxy import create_app

app = create_app(Settings.load())
```

Run this ASGI application with your service runner. The app lifespan closes owned runtime resources. `create_app` also accepts an injected repository, evaluator, and upstream transport for controlled integrations. When bypassing the CLI, enforce listener authentication and deployment constraints yourself.

## Handle failures explicitly

Read `error.code` and HTTP status. Do not retry a `policy_blocked`, `output_blocked`, or `operation_denied` request unchanged. A `budget_exceeded` response needs more capacity or a configured eligible fallback. Neither upstream errors nor classifier unavailability trigger budget fallback automatically. See [HTTP reference](../../reference/http/) and [troubleshooting](../../operations/troubleshooting/).
