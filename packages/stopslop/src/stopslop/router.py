"""Inject local policy enforcement into a standard synchronous OpenAI client."""
import json
import httpx
from .config import Settings
from .policy import Policy, PolicyError


class UpstreamTransport(httpx.BaseTransport):
    """Honor environment proxy routing, which a bare HTTPTransport bypasses."""
    def __init__(self, settings):
        self.client = httpx.Client(timeout=settings.timeout, follow_redirects=False)

    def handle_request(self, request):
        return self.client.send(request, stream=True)

    def close(self):
        self.client.close()


class PolicyRouter(httpx.BaseTransport):
    def __init__(self, settings: Settings, upstream: httpx.BaseTransport | None = None, jev_transport=None, evaluator=None):
        self.policy = Policy(settings, jev_transport, evaluator)
        self.upstream = upstream if upstream is not None else UpstreamTransport(settings)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return httpx.Response(400, json={"error": {"code": "unsupported_endpoint"}})
        try:
            route = self.policy.route(json.loads(request.read()))
        except (ValueError, UnicodeError) as error:
            status = error.status if isinstance(error, PolicyError) else 400
            code = error.code if isinstance(error, PolicyError) else "invalid_json"
            rules = error.rules if isinstance(error, PolicyError) else []
            return httpx.Response(status, json={"error": {"code": code, "message": code, "rules": rules, "risks": error.risks if isinstance(error, PolicyError) else {}}})
        headers = dict(request.headers)
        for name in ("host", "content-length", "authorization"):
            headers.pop(name, None)
        headers["authorization"] = f"Bearer {route.key}"
        outbound = httpx.Request("POST", route.base_url.rstrip("/") + "/chat/completions",
                                 headers=headers, json=route.payload, extensions=request.extensions)
        response = self.upstream.handle_request(outbound)
        if response.status_code >= 500:
            status = response.status_code
            response.close()
            return httpx.Response(status, json={"error": {"code": "upstream_error", "message": "Chat backend temporarily unavailable"}})
        if route.replacements and not response.is_error:
            try:
                body = route.restore(json.loads(response.read()))
            except (ValueError, UnicodeError):
                response.close()
                return httpx.Response(502, json={"error": {"code": "upstream_unavailable"}})
            headers = dict(response.headers)
            for name in ("content-length", "content-encoding", "transfer-encoding"):
                headers.pop(name, None)
            status = response.status_code
            response.close()
            response = httpx.Response(status, json=body, headers=headers)
        response.headers["X-StopSlop-Risks"] = json.dumps(route.risks)
        response.headers["X-StopSlop-Action"] = route.action
        response.headers["X-StopSlop-Rules"] = ",".join(route.rules)
        return response

    def close(self):
        self.upstream.close()
