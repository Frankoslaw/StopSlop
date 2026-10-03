"""Optional HTTP server sharing policy with the in-process router."""
import asyncio
import json
from contextlib import asynccontextmanager
import httpx
from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .config import Settings
from .policy import Policy, PolicyError

def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None, jev_transport=None, evaluator=None) -> FastAPI:
    policy = Policy(settings, jev_transport, evaluator)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            policy.runtime.close()

    app = FastAPI(title="StopSlop", lifespan=lifespan)

    app.state.policy = policy

    @app.get("/health")
    async def health():
        return {"status": "ok", "policy": settings.policy}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        try:
            payload = await request.json()
        except ValueError:
            policy.runtime.request_started()
            policy.runtime.violation("invalid_json", [])
            return JSONResponse({"error": {"code": "invalid_json"}}, status_code=400)
        routed = False
        try:
            client_id = await run_in_threadpool(policy.authenticate, request.headers.get("authorization", ""))
            routed = True
            admission = asyncio.create_task(run_in_threadpool(policy.route, payload, client_id))
            try:
                route = await asyncio.shield(admission)
            except asyncio.CancelledError:
                def release_when_ready(task):
                    try:
                        prepared = task.result()
                    except BaseException:
                        return
                    policy.runtime.finish(prepared.ticket, failed=True)
                admission.add_done_callback(release_when_ready)
                raise
        except PolicyError as error:
            if not routed:
                policy.runtime.request_started()
                policy.runtime.violation(error.code, error.rules, direction="input", action="block")
            return JSONResponse({"error": {"code": error.code, "rules": error.rules, "risks": error.risks}}, status_code=error.status)
        try:
            async with httpx.AsyncClient(timeout=settings.timeout, transport=transport, follow_redirects=False) as client:
                response = await client.post(route.base_url.rstrip("/") + "/chat/completions",
                                             json=route.payload, headers={"Authorization": f"Bearer {route.key}"})
            try:
                body = response.json()
            except ValueError:
                body = None
        except asyncio.CancelledError:
            policy.runtime.finish(route.ticket, failed=True)
            raise
        except (httpx.HTTPError, ValueError):
            policy.runtime.finish(route.ticket, failed=True)
            return JSONResponse({"error": {"code": "upstream_unavailable"}}, status_code=502)
        if not response.is_success:
            policy.runtime.finish(route.ticket, body, route.action, failed=True)
            return JSONResponse({"error": {"code": "upstream_error", "status": response.status_code}},
                                status_code=response.status_code if response.status_code >= 400 else 502)
        try:
            checked, output_action, output_rules, output_risks = await run_in_threadpool(policy.inspect_output, route, body)
        except PolicyError as error:
            policy.runtime.finish(route.ticket, body, "output_blocked")
            if error.code != "output_blocked":
                policy.runtime.violation(error.code, error.rules, direction="output", action="block", risks=error.risks,
                                         client_id=route.client_id, model=route.payload["model"])
            return JSONResponse({"error": {"code": error.code, "rules": error.rules, "risks": error.risks}}, status_code=error.status)
        except BaseException:
            policy.runtime.finish(route.ticket, body, "output_failed")
            raise
        policy.runtime.finish(route.ticket, body, route.action)
        body = checked
        return JSONResponse(body, status_code=response.status_code,
                            headers={"X-StopSlop-Risks": json.dumps(route.risks), "X-StopSlop-Action": route.action,
                                     "X-StopSlop-Rules": ",".join(route.rules), "X-StopSlop-Output-Action": output_action,
                                     "X-StopSlop-Output-Rules": ",".join(output_rules),
                                     "X-StopSlop-Output-Risks": json.dumps(output_risks)})
    return app
