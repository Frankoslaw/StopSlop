import asyncio
import json

import httpx
import pytest

from stopslop.config import Settings
from stopslop_proxy import create_app


def send(body, handler, **settings):
    app = create_app(Settings(key="secret", **settings), httpx.MockTransport(handler))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.post("/api/chat", json=body)
    return asyncio.run(run())


def test_native_translation_and_checked_output():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 32
        assert payload["model"] != "client-model"
        assert "jane@example.org" not in payload["messages"][0]["content"]
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "jane@example.org"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
    response = send({"model": "client-model", "messages": [{"role": "user", "content": "jane@example.org"}],
                     "stream": False, "options": {"num_predict": 32}}, handler, policy="filter")
    assert response.status_code == 200
    assert "jane@example.org" not in response.json()["message"]["content"]
    assert response.json()["done"] is True
    assert response.json()["eval_count"] == 2
    assert response.headers["x-stopslop-action"] == "filter"


@pytest.mark.parametrize("extra", [{}, {"stream": True}, {"stream": False, "tools": []},
                                  {"stream": False, "options": {"seed": 1}},
                                  {"stream": False, "options": {"num_predict": -1}}])
def test_unsupported_native_requests_do_not_forward(extra):
    def handler(request):
        pytest.fail("Unsupported native payload reached upstream")
    response = send({"model": "model", "messages": [{"role": "user", "content": "hello"}], **extra}, handler)
    assert response.status_code == 400


def test_native_input_block_does_not_forward():
    def handler(request):
        pytest.fail("Blocked native payload reached upstream")
    response = send({"model": "model", "messages": [{"role": "user", "content": "jane@example.org"}],
                     "stream": False}, handler)
    assert response.status_code == 403
