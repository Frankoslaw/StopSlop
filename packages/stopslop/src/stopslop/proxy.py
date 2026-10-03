"""Optional HTTP server sharing policy with the in-process router."""
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .config import Settings
from .policy import Policy, PolicyError

def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    app = FastAPI(title="StopSlop")
    policy = Policy(settings)

    @app.get("/health")
    async def health():
        return {"status": "ok", "policy": settings.policy}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"error": {"code": "invalid_json"}}, status_code=400)
        try:
            route = policy.route(payload)
        except PolicyError as error:
            return JSONResponse({"error": {"code": error.code, "rules": error.rules}}, status_code=error.status)
        try:
            async with httpx.AsyncClient(timeout=settings.timeout, transport=transport, follow_redirects=False) as client:
                response = await client.post(route.base_url.rstrip("/") + "/chat/completions",
                                             json=route.payload, headers={"Authorization": f"Bearer {route.key}"})
            body = response.json()
        except (httpx.HTTPError, ValueError):
            return JSONResponse({"error": {"code": "upstream_unavailable"}}, status_code=502)
        if response.is_error:
            body = {"error": {"code": "upstream_error", "status": response.status_code}}
        return JSONResponse(body, status_code=response.status_code,
                            headers={"X-StopSlop-Action": route.action, "X-StopSlop-Rules": ",".join(route.rules)})
    return app
