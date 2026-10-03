import json
import httpx
import pytest
from openai import OpenAI, PermissionDeniedError, RateLimitError
from stopslop.config import Settings
from stopslop.router import PolicyRouter
from stopslop_demo.cli import chat
from cases import CASES


def settings(policy):
    return Settings(main_key="main-secret", policy=policy,
                    fallback_base_url="https://fallback.example/v1", fallback_model="fallback",
                    fallback_key="fallback-secret")


def completion_response():
    return {"id": "demo", "object": "chat.completion", "created": 0, "model": "mock",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "Warsaw"},
                         "finish_reason": "stop"}]}


@pytest.mark.parametrize("policy", ["block", "filter", "redirect", "passthrough"])
@pytest.mark.parametrize("case", [c for c in CASES if not c.known_gap], ids=lambda c: c.id)
def test_sdk_policy_outcomes(policy, case):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=completion_response())
    configuration = settings(policy)
    router = PolicyRouter(configuration, httpx.MockTransport(upstream))
    with OpenAI(base_url=configuration.main_base_url, api_key=configuration.main_key,
                max_retries=0, http_client=httpx.Client(transport=router)) as client:
        payload = {"model": "untrusted-model", "messages": [{"role": "user", "content": case.prompt}]}
        if policy == "block" and case.expected_rules:
            with pytest.raises(PermissionDeniedError) as error:
                client.chat.completions.create(**payload)
            assert error.value.body["code"] == "policy_blocked"
            assert not calls
            return
        assert client.chat.completions.create(**payload).choices[0].message.content == "Warsaw"
    assert len(calls) == 1
    forwarded = json.loads(calls[0].content)
    sensitive = bool(case.expected_rules)
    redirected = policy == "redirect" and sensitive
    assert calls[0].url.host == ("fallback.example" if redirected else "integrate.api.nvidia.com")
    assert calls[0].headers["authorization"] == ("Bearer fallback-secret" if redirected else "Bearer main-secret")
    assert forwarded["model"] == ("fallback" if redirected else configuration.main_model)
    if policy == "filter" and sensitive:
        assert forwarded["messages"][0]["content"] != case.prompt
        assert not router.policy.detector.scan(forwarded["messages"][0]["content"])
    else:
        assert forwarded["messages"][0]["content"] == case.prompt


def test_demo_uses_standard_sdk_and_request_timeout(capsys):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=completion_response())
    configuration = settings("block")
    with OpenAI(base_url=configuration.main_base_url, api_key=configuration.main_key, timeout=12,
                max_retries=0, http_client=httpx.Client(transport=PolicyRouter(configuration, httpx.MockTransport(upstream)))) as client:
        assert chat(client, configuration.main_model, [{"role": "user", "content": "Capital of Poland?"}]) == "Warsaw"
    assert "Warsaw" in capsys.readouterr().out
    assert calls[0].extensions["timeout"]["read"] == 12
    payload = json.loads(calls[0].content)
    assert payload["max_tokens"] == 1024
    assert payload["stream"] is False


def test_rate_limit_is_not_retried():
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"message": "rate limit", "code": "rate_limit"}})
    configuration = settings("block")
    with OpenAI(base_url=configuration.main_base_url, api_key=configuration.main_key,
                max_retries=0, http_client=httpx.Client(transport=PolicyRouter(configuration, httpx.MockTransport(upstream)))) as client:
        with pytest.raises(RateLimitError):
            chat(client, configuration.main_model, [{"role": "user", "content": "Hello"}])
    assert len(calls) == 1


@pytest.mark.parametrize("case", [c for c in CASES if c.known_gap], ids=lambda c: c.id)
@pytest.mark.xfail(strict=True, reason="Regex cannot infer NDA context, decode word numbers or reliably detect obfuscated names")
def test_semantic_or_obfuscated_content_is_detected(case):
    found = {m.rule_id for m in PolicyRouter(settings("block"), httpx.MockTransport(lambda r: None)).policy.detector.scan(case.prompt)}
    assert ("polish_name" in found) if case.id == "unicode" else bool(found)


@pytest.mark.parametrize("single_prompt", [True, False])
def test_demo_cli_single_and_interactive(monkeypatch, capsys, single_prompt):
    from stopslop_demo import cli
    calls = []
    def upstream(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=completion_response())
    configuration = settings("block")
    monkeypatch.setattr(cli.Settings, "load", lambda *a, **k: configuration)
    monkeypatch.setattr(cli, "PolicyRouter", lambda s: PolicyRouter(s, httpx.MockTransport(upstream)))
    monkeypatch.setattr("sys.argv", ["stopslop-demo", "Capital of Poland?"] if single_prompt else ["stopslop-demo"])
    prompts = iter(["Contact Jan Kowalski", "Capital of Poland?", "/quit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(prompts))
    cli.main()
    assert len(calls) == 1
    assert calls[0]["messages"] == [{"role": "system", "content": "You are a helpful assistant."},
                                    {"role": "user", "content": "Capital of Poland?"}]
    output = capsys.readouterr()
    assert "Warsaw" in output.out
    if not single_prompt:
        assert "policy_blocked" in output.err
