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
@pytest.mark.parametrize("case", [c for c in CASES if not c.semantic_rule], ids=lambda c: c.id)
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
    assert payload["max_tokens"] == 96
    assert payload["stream"] is False
    assert payload["reasoning_budget"] == 0
    assert payload["temperature"] == 0.2


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


@pytest.mark.parametrize("case", [c for c in CASES if c.semantic_rule], ids=lambda c: c.id)
def test_semantic_cases_use_jev_probabilities_and_enforce_actions(case):
    from pathlib import Path
    from stopslop.policy import Policy, PolicyError
    calls = []
    def jev(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["state"]["messages"][0]["content"] == case.prompt
        assert any(case.semantic_rule in name for name in body["questions"])
        return httpx.Response(200, json={"answers": {
            name: {"type": "noul", "noul": .95 if name.endswith(case.semantic_rule) else .01}
            for name in body["questions"]}})
    configuration = Settings(main_key="cloud", jev_key="test", policy_file=str(
        Path(__file__).resolve().parents[1] / "policy.json"))
    policy = Policy(configuration, httpx.MockTransport(jev))
    payload = {"messages": [{"role": "user", "content": case.prompt}]}
    if case.semantic_rule == "confidential_semantic":
        with pytest.raises(PolicyError, match="policy_blocked") as caught:
            policy.route(payload)
        assert case.semantic_rule in caught.value.rules
    else:
        route = policy.route(payload)
        assert route.action == "filter"
        assert case.semantic_rule in route.rules
        filtered = route.payload["messages"][0]["content"]
        assert case.prompt not in filtered
        assert route.restore(filtered) == case.prompt
    assert len(calls) == 1


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
    monkeypatch.setattr("sys.argv", ["stopslop-demo", "--policy", "block", "Capital of Poland?"] if single_prompt else ["stopslop-demo", "--policy", "block"])
    prompts = iter(["Contact Jan Kowalski", "Capital of Poland?", "/quit"])
    monkeypatch.setattr("builtins.input", lambda *a: next(prompts))
    cli.main()
    assert len(calls) == 1
    assert calls[0]["messages"][-1] == {"role": "user", "content": "Capital of Poland?"}
    assert len(calls[0]["messages"]) == 2
    output = capsys.readouterr()
    assert "Warsaw" in output.out
    if not single_prompt:
        assert "action=block" in output.err


@pytest.mark.parametrize("options", [
    {"reasoning_budget": "Jan Kowalski"},
    {"reasoning_budget": True},
    {"reasoning_budget": 32769},
    {"chat_template_kwargs": {"hidden": "Jan Kowalski"}},
    {"chat_template_kwargs": {"enable_thinking": "false"}},
])
def test_reasoning_options_cannot_carry_unscanned_text(options):
    from stopslop.policy import Policy, PolicyError
    with pytest.raises(PolicyError):
        Policy(settings("block")).route({"messages": [{"role": "user", "content": "Hello"}], **options})


def test_other_models_do_not_receive_nvidia_thinking_options():
    from types import SimpleNamespace
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Hello"))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(headers={}, parse=lambda: create(**kwargs))))))
    chat(client, "other-model", [{"role": "user", "content": "Hello"}])
    assert captured["extra_body"] == {}



def test_demo_shows_final_answer_after_hosted_thinking_delimiter(capsys):
    from types import SimpleNamespace
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        with_raw_response=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(headers={}, parse=lambda: SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="reasoning text</think>Final answer"))]))))))
    assert chat(client, "other-model", [{"role": "user", "content": "Hello"}]) == "Final answer"
    assert capsys.readouterr().out == "Final answer\n"
