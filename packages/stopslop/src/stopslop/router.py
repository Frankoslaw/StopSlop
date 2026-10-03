"""Inject local policy enforcement into a standard synchronous OpenAI client."""
import json
import httpx
from .config import Settings
from .policy import Policy, PolicyError
from .http_limits import decode_body


class UpstreamTransport(httpx.BaseTransport):
    """Honor environment proxy routing, which a bare HTTPTransport bypasses."""
    def __init__(self, settings):
        self.client = httpx.Client(timeout=settings.timeout, follow_redirects=False)

    def handle_request(self, request):
        return self.client.send(request, stream=True)

    def close(self):
        self.client.close()


class PolicyRouter(httpx.BaseTransport):
    def __init__(self, settings: Settings, upstream: httpx.BaseTransport | None = None, jev_transport=None, evaluator=None, repository=None):
        self.policy = Policy(settings, jev_transport, evaluator, repository)
        self.upstream = upstream if upstream is not None else UpstreamTransport(settings)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return httpx.Response(400, json={"error": {"code": "unsupported_endpoint"}})
        routed = False
        try:
            client_id = self.policy.authenticate(request.headers.get("authorization", ""))
            payload = decode_body(request.stream, self.policy.settings.max_request_bytes)
            routed = True
            route = self.policy.route(payload, client_id)
        except (ValueError, UnicodeError) as error:
            status = error.status if isinstance(error, PolicyError) else 400
            code = error.code if isinstance(error, PolicyError) else "invalid_json"
            rules = error.rules if isinstance(error, PolicyError) else []
            if not routed:
                self.policy.runtime.request_started()
                self.policy.runtime.violation(code, rules, direction="input", action="block")
            return httpx.Response(status, json={"error": {"code": code, "message": code, "rules": rules, "risks": error.risks if isinstance(error, PolicyError) else {}}})
        headers = dict(request.headers)
        for name in ("host", "content-length", "authorization"):
            headers.pop(name, None)
        headers["authorization"] = f"Bearer {route.key}"
        outbound = httpx.Request("POST", route.base_url.rstrip("/") + "/chat/completions",
                                 headers=headers, json=route.payload, extensions=request.extensions)
        try:
            response = self.upstream.handle_request(outbound)
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > self.policy.settings.max_response_bytes:
                    response.close()
                    self.policy.finish(route, action="output_blocked", status="response_too_large")
                    return httpx.Response(502, json={"error": {"code": "response_too_large"}})
                content.extend(chunk)
            try:
                usage_body = json.loads(content)
            except ValueError:
                usage_body = None
        except BaseException:
            self.policy.finish(route, failed=True)
            raise
        if not response.is_success:
            status = response.status_code
            self.policy.finish(route, usage_body, failed=True)
            response.close()
            return httpx.Response(status if status >= 400 else 502,
                                  json={"error": {"code": "upstream_error", "message": "Chat backend unavailable"}})
        try:
            body, output_action, output_rules, output_risks = self.policy.inspect_output(route, usage_body)
        except PolicyError as error:
            # Generated work is billed even when its reply is withheld.
            self.policy.finish(route, usage_body, "output_blocked", status=error.code)
            if error.code != "output_blocked":
                self.policy.runtime.violation(error.code, error.rules, direction="output", action="block", risks=error.risks,
                                              client_id=route.client_id, model=route.payload["model"])
            response.close()
            return httpx.Response(error.status, json={"error": {"code": error.code, "message": error.code,
                                                               "rules": error.rules, "risks": error.risks}})
        except BaseException:
            self.policy.finish(route, usage_body, "output_failed")
            response.close()
            raise
        self.policy.finish(route, usage_body, response=body)
        headers = dict(response.headers)
        for name in ("content-length", "content-encoding", "transfer-encoding", "set-cookie"):
            headers.pop(name, None)
        status = response.status_code
        response.close()
        response = httpx.Response(status, json=body, headers=headers)
        response.headers["X-StopSlop-Output-Action"] = output_action
        response.headers["X-StopSlop-Output-Rules"] = ",".join(output_rules)
        response.headers["X-StopSlop-Output-Risks"] = json.dumps(output_risks)
        response.headers["X-StopSlop-Risks"] = json.dumps(route.risks)
        response.headers["X-StopSlop-Action"] = route.action
        response.headers["X-StopSlop-Rules"] = ",".join(route.rules)
        response.headers["X-StopSlop-Budget-Fallback"] = route.budget_fallback or "none"
        response.headers["X-StopSlop-Model"] = route.payload["model"]
        return response

    def close(self):
        try:
            self.upstream.close()
        finally:
            self.policy.runtime.close()
