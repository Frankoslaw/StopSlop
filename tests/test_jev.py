import json

import httpx
import pytest

from stopslop.config import Settings
from stopslop.policy import Policy, PolicyError
from stopslop.router import PolicyRouter


def settings(tmp_path, action="block", threshold=75):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": 1, "default_action": "filter", "rules": [
        {"id": "nda", "description": "Do not disclose NDA protected information.",
         "threshold": threshold, "action": action}]}))
    return Settings(classifier="jev", policy_file=str(path), key="cloud", jev_key="test",
                    local_model="local-model")


def evaluator(probability):
    def respond(request):
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test"
        body = json.loads(request.content)
        assert body["model"] == "jev-latest"
        assert body["state"]["messages"]
        return httpx.Response(200, json={"answers": {
            name: {"type": "noul", "noul": probability} for name in body["questions"]}})
    return httpx.MockTransport(respond)


def payload(text="Our unreleased revenue is two million"):
    return {"messages": [{"role": "user", "content": text}]}


@pytest.mark.parametrize("risk,blocked", [(0.749, False), (0.75, True), (1, True)])
def test_threshold_boundary(tmp_path, risk, blocked):
    policy = Policy(settings(tmp_path), evaluator(risk))
    if blocked:
        with pytest.raises(PolicyError) as caught:
            policy.route(payload())
        assert caught.value.code == "policy_blocked"
        assert caught.value.risks == {"m0_nda": risk * 100}
    else:
        assert policy.route(payload()).action == "allow"


@pytest.mark.parametrize("action", ["filter", "local", "allow"])
def test_semantic_actions(tmp_path, action):
    route = Policy(settings(tmp_path, action), evaluator(.9)).route(payload())
    assert route.action == action
    if action == "filter":
        content = route.payload["messages"][0]["content"]
        assert content == "[ANON:nda:1]"
        assert route.restore(content) == payload()["messages"][0]["content"]
    if action == "local":
        assert route.base_url.startswith("http://127.0.0.1")


@pytest.mark.parametrize("probability", [None, True, "0.9", -0.1, 1.1, float("nan")])
def test_invalid_risks_fail_closed(tmp_path, probability):
    def respond(request):
        # Raw JSON permits exercising nonfinite numbers without httpx's encoder.
        return httpx.Response(200, text=json.dumps({"answers": {
            "m0_nda": {"type": "noul", "noul": probability}}}))
    with pytest.raises(PolicyError, match="jev_unavailable"):
        Policy(settings(tmp_path), httpx.MockTransport(respond)).route(payload())


@pytest.mark.parametrize("failure", ["timeout", "auth", "missing"])
def test_jev_failure_never_reaches_chat(tmp_path, failure):
    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(401 if failure == "auth" else 200, json={})
    def upstream(request):
        pytest.fail("Failed assessment reached chat")
    with httpx.Client(transport=PolicyRouter(settings(tmp_path), httpx.MockTransport(upstream),
                                            httpx.MockTransport(respond))) as client:
        response = client.post("https://test/v1/chat/completions", json=payload())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "jev_unavailable"


def test_missing_key_rejected(tmp_path):
    from dataclasses import replace
    with pytest.raises(ValueError, match="STOPSLOP_JEV_KEY"):
        Policy(replace(settings(tmp_path), jev_key=""))


@pytest.mark.parametrize("threshold", [-1, 101, True, "75", float("nan")])
def test_invalid_threshold(tmp_path, threshold):
    with pytest.raises(ValueError):
        Policy(settings(tmp_path, threshold=threshold))


def test_deterministic_block_skips_jev(tmp_path):
    config = settings(tmp_path)
    data = json.loads(open(config.policy_file).read())
    data["rules"].append({"id": "secret", "action": "block"})
    from pathlib import Path
    Path(config.policy_file).write_text(json.dumps(data))
    def respond(request):
        pytest.fail("Deterministic block called Jev")
    with pytest.raises(PolicyError, match="policy_blocked"):
        Policy(config, httpx.MockTransport(respond)).route(payload("password=hidden"))


def test_deterministic_ignores_injected_evaluator_and_needs_no_key(tmp_path):
    from dataclasses import replace
    class ForbiddenEvaluator:
        name, model = "llm", "unused"
        def assess(self, messages, rules):
            pytest.fail("Deterministic mode called semantic evaluator")
    config = replace(settings(tmp_path), deterministic=True, jev_key="")
    route = Policy(config, evaluator=ForbiddenEvaluator()).route(payload())
    assert route.action == "allow" and not route.risks
    route = Policy(config, evaluator=ForbiddenEvaluator()).route(payload("jane@example.org"))
    assert route.action == "filter"


def test_llm_adapter_can_replace_jev_without_jev_key(tmp_path):
    from dataclasses import replace
    from stopslop.evaluators import LLMEvaluator
    config = replace(settings(tmp_path, "filter"), jev_key="")
    def respond(request):
        assert request.url.host == "integrate.api.nvidia.com"
        assert request.headers["Authorization"] == "Bearer cloud"
        body = json.loads(request.content)
        assert body["model"] == config.model and body["reasoning_budget"] == 0
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"m0_nda":0.9}'}}]})
    route = Policy(config, evaluator=LLMEvaluator(config, httpx.MockTransport(respond))).route(payload())
    assert route.action == "filter" and route.risks == {"m0_nda": 90}


def test_llm_invalid_output_fails_closed(tmp_path):
    from stopslop.evaluators import LLMEvaluator
    config = settings(tmp_path)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"content": '{"m0_nda":true}'}}]}))
    with pytest.raises(PolicyError, match="llm_unavailable"):
        Policy(config, evaluator=LLMEvaluator(config, transport)).route(payload())


def test_logs_show_backend_threshold_rule_and_no_content(tmp_path, capsys):
    import logging
    from stopslop_demo.logs import configure_logs
    logger = configure_logs("never")
    try:
        policy = Policy(settings(tmp_path, "filter"), evaluator(.8))
        policy.route(payload())
        output = capsys.readouterr().err
        assert "[WARN] action=filter" in output
        assert "rule=nda message=" not in output
        assert "risk=80.0% threshold=75.0%" in output
        assert "chat backend=main" in output
        assert "unreleased revenue" not in output and "Bearer" not in output
        with pytest.raises(PolicyError):
            Policy(settings(tmp_path), evaluator(.8)).route(payload())
        assert "[ERROR] action=block" in capsys.readouterr().err
        Policy(settings(tmp_path), evaluator(.1)).route(payload())
        assert "[INFO] action=allow" in capsys.readouterr().err
    finally:
        for handler in list(logger.handlers):
            if getattr(handler, "stopslop_demo", False):
                logger.removeHandler(handler)
                handler.close()
        logger.propagate = True


def test_color_formatter():
    import logging
    from stopslop_demo.logs import ColoredFormatter
    for level, code, label in [(logging.INFO, "36", "INFO"), (logging.WARNING, "33", "WARN"),
                               (logging.ERROR, "31", "ERROR")]:
        record = logging.LogRecord("test", level, "", 0, "rule validated", (), None)
        assert ColoredFormatter(True).format(record) == f"\033[{code}m[{label}] rule validated\033[0m"
        assert "\033" not in ColoredFormatter(False).format(record)


def test_demo_deterministic_flag_loads_unified_policy(tmp_path, monkeypatch, capsys):
    from dataclasses import replace
    from stopslop_demo import cli
    config = replace(settings(tmp_path), jev_key="")
    captured = []
    def load(*args, **overrides):
        captured.append(overrides)
        return replace(config, deterministic=overrides["deterministic"])
    def upstream(request):
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
            "model": "mock", "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": "Hello"}}]})
    monkeypatch.setattr(cli.Settings, "load", load)
    monkeypatch.setattr(cli, "PolicyRouter", lambda s: PolicyRouter(s, httpx.MockTransport(upstream)))
    monkeypatch.setattr("sys.argv", ["stopslop-demo", "--deterministic", "--color", "never", "Hello"])
    cli.main()
    output = capsys.readouterr()
    assert captured[0]["deterministic"] is True
    assert "Semantic rules skipped: deterministic mode" in output.err
    assert "backend=main" in output.err
    assert "Hello" in output.out
