"""Inject local policy enforcement into a standard synchronous OpenAI client."""
import json
import httpx
from .config import Settings
from .policy import Policy, PolicyError

class PolicyRouter(httpx.BaseTransport):
    def __init__(self, settings: Settings, upstream: httpx.BaseTransport | None = None):
        self.policy = Policy(settings)
        self.upstream = upstream if upstream is not None else httpx.HTTPTransport(retries=0)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "POST" or not request.url.path.endswith("/chat/completions"):
            return httpx.Response(400, json={"error": {"code": "unsupported_endpoint"}})
        try:
            route = self.policy.route(json.loads(request.read()))
        except (ValueError, UnicodeError) as error:
            status = error.status if isinstance(error, PolicyError) else 400
            code = error.code if isinstance(error, PolicyError) else "invalid_json"
            rules = error.rules if isinstance(error, PolicyError) else []
            return httpx.Response(status, json={"error": {"code": code, "message": code, "rules": rules}})
        headers = dict(request.headers)
        for name in ("host", "content-length", "authorization"):
            headers.pop(name, None)
        headers["authorization"] = f"Bearer {route.key}"
        outbound = httpx.Request("POST", route.base_url.rstrip("/") + "/chat/completions",
                                 headers=headers, json=route.payload, extensions=request.extensions)
        response = self.upstream.handle_request(outbound)
        response.headers["X-StopSlop-Action"] = route.action
        response.headers["X-StopSlop-Rules"] = ",".join(route.rules)
        return response

    def close(self):
        self.upstream.close()
