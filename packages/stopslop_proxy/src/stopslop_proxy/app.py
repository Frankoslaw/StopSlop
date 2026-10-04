"""Optional HTTP server sharing policy with the in-process router."""
import asyncio
import json
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import httpx
from starlette.concurrency import run_in_threadpool
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from stopslop.config import Settings
from stopslop.policy import Policy, PolicyError
from stopslop.http_limits import request_payload


def ollama_payload(body):
    """Translate the supported native text-chat subset, rejecting unknown fields."""
    if (not isinstance(body, dict) or set(body) - {"model", "messages", "stream", "options"}
            or body.get("stream", True) is not False):
        raise PolicyError("unsupported_payload", 400)
    if not isinstance(body.get("model"), str) or not body["model"]:
        raise PolicyError("invalid_model", 400)
    options = body.get("options", {})
    if not isinstance(options, dict) or set(options) - {"temperature", "top_p", "num_predict"}:
        raise PolicyError("unsupported_payload", 400)
    payload = {"model": body["model"], "messages": body.get("messages"), "stream": False}
    for name, value in options.items():
        payload["max_tokens" if name == "num_predict" else name] = value
    return payload

def create_app(settings: Settings, transport: httpx.AsyncBaseTransport | None = None, jev_transport=None, evaluator=None, repository=None) -> FastAPI:
    policy = Policy(settings, jev_transport, evaluator, repository)

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

    @app.post("/api/chat")
    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        native = request.url.path == "/api/chat"
        routed = False
        try:
            client_id = await run_in_threadpool(policy.authenticate, request.headers.get("authorization", ""))
            payload = await request_payload(request, settings.max_request_bytes)
            if native:
                payload = ollama_payload(payload)
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
                    policy.finish(prepared, failed=True)
                admission.add_done_callback(release_when_ready)
                raise
        except PolicyError as error:
            if not routed:
                policy.runtime.request_started()
                policy.runtime.violation(error.code, error.rules, direction="input", action="block")
            return JSONResponse({"error": {"code": error.code, "rules": error.rules, "risks": error.risks}}, status_code=error.status)
        try:
            async with httpx.AsyncClient(timeout=settings.timeout, transport=transport, follow_redirects=False) as client:
                async with client.stream("POST", route.base_url.rstrip("/") + "/chat/completions",
                                         json=route.payload, headers={"Authorization": f"Bearer {route.key}"}) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > settings.max_response_bytes:
                            policy.finish(route, action="output_blocked", status="response_too_large")
                            return JSONResponse({"error": {"code": "response_too_large"}}, status_code=502)
                        content.extend(chunk)
                    try:
                        body = json.loads(content)
                    except (ValueError, UnicodeError):
                        body = None
        except asyncio.CancelledError:
            policy.finish(route, failed=True)
            raise
        except (httpx.HTTPError, ValueError):
            policy.finish(route, failed=True)
            return JSONResponse({"error": {"code": "upstream_unavailable"}}, status_code=502)
        if not response.is_success:
            policy.finish(route, body, failed=True)
            return JSONResponse({"error": {"code": "upstream_error", "status": response.status_code}},
                                status_code=response.status_code if response.status_code >= 400 else 502)
        try:
            checked, output_action, output_rules, output_risks = await run_in_threadpool(policy.inspect_output, route, body)
        except PolicyError as error:
            policy.finish(route, body, "output_blocked", status=error.code)
            if error.code != "output_blocked":
                policy.runtime.violation(error.code, error.rules, direction="output", action="block", risks=error.risks,
                                         client_id=route.client_id, model=route.payload["model"])
            return JSONResponse({"error": {"code": error.code, "rules": error.rules, "risks": error.risks}}, status_code=error.status)
        except BaseException:
            policy.finish(route, body, "output_failed")
            raise
        policy.finish(route, body, response=checked)
        body = checked
        if native:
            choices = checked["choices"]
            usage = checked.get("usage") or {}
            body = {"model": route.payload["model"],
                    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "message": choices[0]["message"] if choices else {"role": "assistant", "content": ""},
                    "done": True, "done_reason": choices[0].get("finish_reason", "stop") if choices else "stop",
                    "prompt_eval_count": usage.get("prompt_tokens", 0),
                    "eval_count": usage.get("completion_tokens", 0)}
        return JSONResponse(body, status_code=response.status_code,
                            headers={"X-StopSlop-Risks": json.dumps(route.risks), "X-StopSlop-Action": route.action,
                                     "X-StopSlop-Rules": ",".join(route.rules), "X-StopSlop-Output-Action": output_action,
                                     "X-StopSlop-Output-Rules": ",".join(output_rules),
                                     "X-StopSlop-Output-Risks": json.dumps(output_risks),
                                     "X-StopSlop-Budget-Fallback": route.budget_fallback or "none",
                                     "X-StopSlop-Model": route.payload["model"]})
    @app.post("/v1/authorize")
    async def authorize(request: Request):
        try:
            client_id = await run_in_threadpool(policy.authenticate, request.headers.get("authorization", ""))
            if not client_id:
                raise PolicyError("unauthorized_client", 401)
            body = await request_payload(request, min(settings.max_request_bytes, 8192))
            if not isinstance(body, dict) or set(body) != {"kind", "resource", "operation"}:
                raise PolicyError("invalid_operation", 400)
            result = await run_in_threadpool(policy.authorize_operation, client_id, body["kind"],
                                            body["resource"], body["operation"])
            return JSONResponse(result)
        except PolicyError as error:
            return JSONResponse({"error": {"code": error.code, "rules": error.rules}}, status_code=error.status)

    return app
