import asyncio
import json
import httpx
import pytest
from stopslop.config import Settings
from stopslop_proxy import create_app
from stopslop.rules import Detector
from cases import CASES


@pytest.mark.parametrize("case", [c for c in CASES if not c.semantic_rule], ids=lambda c: c.id)
def test_rules(case):
    found = {m.rule_id for m in Detector().scan(case.prompt)}
    assert set(case.expected_rules).issubset(found)
    if not case.expected_rules:
        assert not found


@pytest.mark.parametrize("policy", ["block", "filter", "redirect", "passthrough"])
def test_policy_controls_actual_outbound_request(policy):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": []})
    settings = Settings(key="main-secret", policy=policy,
                        fallback_base_url="https://fallback.example/v1", fallback_model="fallback",
                        fallback_key="fallback-secret")
    app = create_app(settings, httpx.MockTransport(handler))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.post("/v1/chat/completions", json={"model": "untrusted-model",
                "messages": [{"role": "system", "content": "Contact Jan Kowalski."},
                             {"role": "user", "content": "Call +48 512 345 678."}]})
    response = asyncio.run(run())
    if policy == "block":
        assert response.status_code == 403
        assert not calls
        return
    assert response.status_code == 200
    assert len(calls) == 1
    payload = json.loads(calls[0].content)
    if policy == "filter":
        assert all(not Detector().scan(m["content"]) for m in payload["messages"])
        assert "Jan Kowalski" not in calls[0].content.decode()
        assert "512 345 678" not in calls[0].content.decode()
    else:
        assert payload["messages"][0]["content"] == "Contact Jan Kowalski."
    if policy == "redirect":
        assert calls[0].url.host == "fallback.example"
        assert calls[0].headers["authorization"] == "Bearer fallback-secret"
        assert payload["model"] == "fallback"
    else:
        assert calls[0].url.host == "integrate.api.nvidia.com"
        assert payload["model"] == settings.model
        assert calls[0].headers["authorization"] == "Bearer main-secret"


@pytest.mark.parametrize("payload", [
    {"messages": [{"role": "user", "content": "ok"}], "stream": True},
    {"messages": [{"role": "user", "content": [{"type": "text", "text": "Jan Kowalski"}]}]},
    {"messages": [{"role": "user", "content": "ok"}], "tools": []},
    {"messages": [{"role": "user", "content": "ok", "name": "Jan Kowalski"}]},
    [], {"messages": []},
])
def test_unsupported_payload_never_forwards(payload):
    def handler(request):
        pytest.fail("unsupported input reached upstream")
    app = create_app(Settings(key="secret"), httpx.MockTransport(handler))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.post("/v1/chat/completions", json=payload)
    assert asyncio.run(run()).status_code == 400


def test_identifier_checksums_and_overlap():
    detector = Detector()
    assert not any(m.rule_id == "pesel" for m in detector.scan("02070803629"))
    assert not any(m.rule_id == "bank_account" for m in detector.scan("PL62 1090 1014 0000 0712 1981 2874"))
    text = "PL61 1090 1014 0000 0712 1981 2874"
    assert detector.redact(text, detector.scan(text)) == "[REDACTED:bank_account]"


def test_redirect_requires_complete_configuration():
    with pytest.raises(ValueError):
        Settings(policy="redirect")


def test_upstream_failure_does_not_leak_or_redirect():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"error": "secret echoed upstream"})
    app = create_app(Settings(key="secret"), httpx.MockTransport(handler))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hello"}]})
    response = asyncio.run(run())
    assert response.status_code == 401
    assert "secret" not in response.text
    assert len(calls) == 1


def test_config_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("STOPSLOP_MODEL=file-model\nSTOPSLOP_POLICY=filter\n")
    monkeypatch.setenv("STOPSLOP_MODEL", "environment-model")
    monkeypatch.delenv("STOPSLOP_POLICY", raising=False)
    assert Settings.load(str(env)).model == "environment-model"
    assert Settings.load(str(env), model="cli-model").model == "cli-model"
    assert Settings.load(str(env)).policy == "filter"


def test_redirect_keeps_benign_requests_on_main():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": []})
    settings = Settings(key="main", policy="redirect", fallback_base_url="https://fallback.example/v1",
                        fallback_model="fallback", fallback_key="fallback")
    app = create_app(settings, httpx.MockTransport(handler))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "What is the capital of Poland?"}]})
    assert asyncio.run(run()).status_code == 200
    assert calls[0].url.host == "integrate.api.nvidia.com"
