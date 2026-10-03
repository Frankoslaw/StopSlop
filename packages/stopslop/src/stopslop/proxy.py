"""Optional HTTP server sharing policy with the in-process router."""
import asyncio
import json
import httpx
from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .config import Settings
from .policy import Policy, PolicyError

def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None, jev_transport=None, evaluator=None) -> FastAPI:
    app = FastAPI(title="StopSlop")
    policy = Policy(settings, jev_transport, evaluator)

    app.state.policy = policy

    @app.get("/health")
    async def health():
        return {"status": "ok", "policy": settings.policy}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        try:
            payload = await request.json()
        except ValueError:
            policy.runtime.violation("invalid_json", [])
            return JSONResponse({"error": {"code": "invalid_json"}}, status_code=400)
        try:
            route = await run_in_threadpool(policy.route, payload)
        except PolicyError as error:
            return JSONResponse({"error": {"code": error.code, "rules": error.rules, "risks": error.risks}}, status_code=error.status)
        try:
            async with httpx.AsyncClient(timeout=settings.timeout, transport=transport, follow_redirects=False) as client:
                response = await client.post(route.base_url.rstrip("/") + "/chat/completions",
                                             json=route.payload, headers={"Authorization": f"Bearer {route.key}"})
            body = response.json()
        except asyncio.CancelledError:
            policy.runtime.finish(route.ticket, failed=True)
            raise
        except (httpx.HTTPError, ValueError):
            policy.runtime.finish(route.ticket, failed=True)
            return JSONResponse({"error": {"code": "upstream_unavailable"}}, status_code=502)
        policy.runtime.finish(route.ticket, body, route.action, response.is_error)
        if response.is_error:
            body = {"error": {"code": "upstream_error", "status": response.status_code}}
        else:
            body = route.restore(body)
        return JSONResponse(body, status_code=response.status_code,
                            headers={"X-StopSlop-Risks": json.dumps(route.risks), "X-StopSlop-Action": route.action, "X-StopSlop-Rules": ",".join(route.rules)})
    return app
