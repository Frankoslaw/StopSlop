import asyncio
import json
from pathlib import Path

import httpx
import pytest

from stopslop.config import Settings
from stopslop.policy import Policy, PolicyError
from stopslop.proxy import create_app
from stopslop.router import PolicyRouter
from stopslop.rules import Detector


@pytest.mark.parametrize("rule,text", [
    ("email", "Contact jane@example.org"),
    ("international_phone", "Call +1 202 555 0123"),
    ("domestic_phone", "Call (202) 555-0123"),
    ("iban", "Pay GB82 WEST 1234 5698 7654 32 please"),
    ("credit_card", "Card 4111 1111 1111 1111"),
    ("personal_name", "Full name: Jane Smith"),
    ("address", "Address: 12 Main Street"),
    ("date_of_birth", "DOB: 1990-01-02"),
    ("national_id", "SSN: 123-45-6789"),
    ("secret", "api_key=super-secret"),
    ("private_key", "-----BEGIN PRIVATE KEY-----\nprivate material\n-----END PRIVATE KEY-----"),
    ("profanity", "This is fucking broken"),
])
def test_builtins(rule, text):
    assert rule in {hit.rule_id for hit in Detector().scan(text)}


def test_invalid_checksums_and_word_boundaries():
    detector = Detector()
    assert not any(hit.rule_id in {"iban", "credit_card"} for hit in detector.scan(
        "GB83 WEST 1234 5698 7654 32, 4111 1111 1111 1112"))
    assert not detector.scan("The assignment uses a bitshift.")


def configuration(tmp_path, rules, **kwargs):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": 1, "default_action": "filter", "rules": rules}))
    return Settings(main_key="cloud", policy_file=str(path), **kwargs)


def payload(text):
    return {"messages": [{"role": "user", "content": text}], "reasoning_budget": 0}


def test_allowed_exceptions_are_scoped_and_custom_rules_block(tmp_path):
    settings = configuration(tmp_path, [
        {"id": "email", "action": "filter", "allowed_patterns": [r"support@example\.com"]},
        {"id": "forbidden", "pattern": r"never process", "action": "block"},
        {"id": "profanity", "action": "allow"},
    ])
    policy = Policy(settings)
    route = policy.route(payload("support@example.com jane@example.org shit"))
    content = route.payload["messages"][0]["content"]
    assert "support@example.com" in content and "shit" in content
    assert "jane@example.org" not in content
    with pytest.raises(PolicyError, match="policy_blocked"):
        policy.route(payload("support@example.com never process"))


@pytest.mark.parametrize("kind", ["router", "proxy"])
@pytest.mark.parametrize("failure", [False, True])
def test_local_routing_mixed_filter_and_no_cloud_fallback(tmp_path, kind, failure):
    settings = configuration(tmp_path, [{"id": "profanity", "action": "local"}], local_model="small-local")
    definition = json.loads(Path(settings.policy_file).read_text())
    definition["output"] = {"rules": [{"id": "profanity", "action": "warn"}]}
    Path(settings.policy_file).write_text(json.dumps(definition))
    calls = []
    def upstream(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1"
        body = json.loads(request.content)
        assert body["model"] == "small-local" and "reasoning_budget" not in body
        text = body["messages"][0]["content"]
        assert "shit" in text and "jane@example.org" not in text
        if failure:
            raise httpx.ConnectError("local unavailable")
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
    if kind == "router":
        with httpx.Client(transport=PolicyRouter(settings, httpx.MockTransport(upstream))) as client:
            if failure:
                with pytest.raises(httpx.ConnectError):
                    client.post("http://test/v1/chat/completions", json=payload("shit jane@example.org"))
            else:
                response = client.post("http://test/v1/chat/completions", json=payload("shit jane@example.org"))
                assert response.json()["choices"][0]["message"]["content"] == "shit jane@example.org"
    else:
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app(settings, httpx.MockTransport(upstream))), base_url="http://test") as client:
                return await client.post("/v1/chat/completions", json=payload("shit jane@example.org"))
        response = asyncio.run(run())
        assert response.status_code == (502 if failure else 200)
    assert len(calls) == 1


def test_block_wins_and_missing_local_model_fails_closed(tmp_path):
    policy = Policy(configuration(tmp_path, [{"id": "secret", "action": "block"}, {"id": "profanity", "action": "local"}]))
    with pytest.raises(PolicyError, match="policy_blocked"):
        policy.route(payload("shit password=hidden jane@example.org"))
    with pytest.raises(PolicyError, match="missing_local_model"):
        policy.route(payload("shit jane@example.org"))


@pytest.mark.parametrize("url", ["https://remote.example/v1", "http://127.0.0.1.evil.example/v1", "file:///tmp", "http://user:pass@localhost/v1"])
def test_local_endpoint_must_be_loopback(url):
    with pytest.raises(ValueError, match="loopback"):
        Settings(local_base_url=url)


@pytest.mark.parametrize("rules", [
    [{"id": "email", "action": "unknown"}],
    [{"id": "email"}, {"id": "email"}],
    [{"id": "custom"}],
    [{"id": "custom", "pattern": "["}],
    [{"id": "email", "pattern": ".*"}],
    [{"id": "email", "allowed_patterns": ["["]}],
    [{"id": "custom", "pattern": ".*", "validator": "unknown"}],
])
def test_invalid_policy_rejected_at_startup(tmp_path, rules):
    with pytest.raises(ValueError):
        Policy(configuration(tmp_path, rules))


def test_shipped_policy():
    policy = Policy(Settings(main_key="cloud", local_model="small-local",
                             policy_file=str(Path(__file__).resolve().parents[1] / "policy.toml"), deterministic=True))
    assert policy.route(payload("Hello")).action == "allow"
    assert policy.route(payload("jane@example.org")).action == "filter"
    assert policy.route(payload("shit")).action == "local"
    with pytest.raises(PolicyError, match="policy_blocked"):
        policy.route(payload("Strictly confidential"))


@pytest.mark.parametrize("kind", ["router", "proxy"])
def test_block_does_not_call_any_model(tmp_path, kind):
    settings = configuration(tmp_path, [{"id": "secret", "action": "block"},
                                         {"id": "profanity", "action": "local"}], local_model="small-local")
    def upstream(request):
        pytest.fail("Blocked request reached a model")
    if kind == "router":
        with httpx.Client(transport=PolicyRouter(settings, httpx.MockTransport(upstream))) as client:
            response = client.post("http://test/v1/chat/completions", json=payload("shit password=hidden jane@example.org"))
    else:
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app(settings, httpx.MockTransport(upstream))), base_url="http://test") as client:
                return await client.post("/v1/chat/completions", json=payload("shit password=hidden jane@example.org"))
        response = asyncio.run(run())
    assert response.status_code == 403
