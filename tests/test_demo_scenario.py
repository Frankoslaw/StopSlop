from dataclasses import replace
import json
from pathlib import Path

import httpx
import pytest

from stopslop.config import Settings
from stopslop.router import PolicyRouter
from stopslop.jev import JevEvaluator
from stopslop_demo.scenarios import SCENARIOS
from stopslop_demo.spinner import Spinner


def config():
    return Settings(metrics_file="", classifier="jev", main_key="mock", jev_key="mock", policy_file=str(
        Path(__file__).resolve().parents[1] / "policy.json"))


def completion():
    return {"id": "mock", "object": "chat.completion", "created": 0, "model": "mock",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "Agenda updated."}}]}


def test_scenario_runs_sequentially_and_every_run_calls_backends(tmp_path, monkeypatch, capsys):
    from stopslop_demo import cli
    calls, assessments = [], []
    def jev(request):
        body = json.loads(request.content)
        assessments.append(body)
        answers = {}
        for name in body["questions"]:
            index = int(name.split("_", 1)[0][1:])
            text = body["state"]["messages"][index]["content"]
            risk = .99 if "Project Kestrel" in text and name.endswith("confidential_semantic") else .01
            answers[name] = {"type": "noul", "noul": risk}
        return httpx.Response(200, json={"answers": answers})
    def upstream(request):
        body = json.loads(request.content)
        calls.append(body)
        assert not any("Jane Smith" in m["content"] or "Project Kestrel" in m["content"] for m in body["messages"])
        return httpx.Response(200, json=completion())
    settings = config()
    monkeypatch.setattr(cli.Settings, "load", lambda *a, **k: settings)
    monkeypatch.setattr(cli, "PolicyRouter", lambda s: PolicyRouter(s, httpx.MockTransport(upstream), evaluator=JevEvaluator(s, httpx.MockTransport(jev))))
    monkeypatch.setattr("sys.argv", ["stopslop-demo", "--scenario", "--color", "never"])
    cli.main()
    assert [len(body["messages"]) for body in calls] == [2, 4, 6]
    assert "[ANON:personal_name:" in calls[-1]["messages"][-1]["content"]
    assert len(assessments) == 7  # Four inputs and three generated replies.
    cli.main()
    assert len(calls) == 6 and len(assessments) == 14
    output = capsys.readouterr()
    assert "Scenario complete" in output.err
    assert "confidential_semantic" in output.err and "action=block" in output.err
    assert "You [4, expected=block]" in output.out
    assert not list(tmp_path.glob("*.json"))



def test_spinner_stops_on_error(capsys):
    import stopslop_demo.spinner as module
    spinner = Spinner(enabled=True)
    with pytest.raises(RuntimeError):
        with spinner:
            raise RuntimeError("mock failure")
    assert spinner.thread is not None and not spinner.thread.is_alive()
    assert module.active is False
    assert "Checking policies" in capsys.readouterr().err


def test_scenario_finishes_with_semantic_not_regex_nda_block():
    from stopslop.policy_file import PolicyFile
    definition = PolicyFile(config().policy_file)
    final = SCENARIOS["nda"][-1]
    assert final.expected_action == "block"
    assert not any(definition.action_for(hit.rule_id) == "block" for hit in definition.scan(final.prompt))


def test_console_handles_unicode_from_hosted_models(monkeypatch):
    import io
    from stopslop_demo.logs import configure_console
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="cp1250")
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setattr("sys.stderr", stream)
    configure_console()
    stream.write("Follow-up\u2011tasks \U0001f680")
    stream.flush()
    assert output.getvalue().decode("utf-8") == "Follow-up\u2011tasks \U0001f680"


@pytest.mark.parametrize("failure,exit_code", [("timeout", 1), ("interrupt", 130)])
def test_demo_wait_failure_is_clean_without_retries(tmp_path, monkeypatch, capsys, failure, exit_code):
    from stopslop_demo import cli
    settings = replace(config(), deterministic=True)
    calls = []
    def upstream(request):
        calls.append(request)
        if failure == "interrupt":
            raise KeyboardInterrupt()
        raise httpx.ReadTimeout("mock timeout", request=request)
    monkeypatch.setattr(cli.Settings, "load", lambda *a, **k: settings)
    monkeypatch.setattr(cli, "PolicyRouter", lambda s: PolicyRouter(s, httpx.MockTransport(upstream)))
    monkeypatch.setattr("sys.argv", ["stopslop-demo", "--policy", "block", "Hello"])
    with pytest.raises(SystemExit) as caught:
        cli.main()
    assert caught.value.code == exit_code
    assert len(calls) == 1
    output = capsys.readouterr().err
    assert "Traceback" not in output
    assert "no retry" in output


def test_progress_is_one_line_and_not_persistent_log_spam(monkeypatch, capsys):
    import logging
    import stopslop_demo.spinner as module
    from stopslop_demo.logs import configure_logs
    logger = configure_logs("never")
    for index in range(100):
        logger.info("Verbose per-rule detail", extra={"progress": f"Running rule ({index + 1}/100): description"})
        logger.info("Risk details", extra={"rule_check": True})
    assert capsys.readouterr().err == ""
    assert module.status == "Running rule (100/100): description"
    monkeypatch.setattr(module.shutil, "get_terminal_size", lambda *args: __import__("os").terminal_size((50, 24)))
    with Spinner(enabled=True) as spinner:
        module.set_status("Running rule (1/20): " + "A long rule description " * 10)
        spinner.stop.wait(.15)
    output = capsys.readouterr().err
    assert "\n" not in output
    assert all(len(line) <= 49 for line in output.split("\r"))


def test_scenario_retries_only_transient_chat_errors(monkeypatch):
    from openai import OpenAI
    from stopslop_demo import cli
    calls = []
    def upstream(request):
        calls.append(json.loads(request.content))
        return httpx.Response(503, text="Temporarily unavailable") if len(calls) == 1 else httpx.Response(200, json=completion())
    settings = replace(config(), deterministic=True)
    monkeypatch.setattr(cli.time, "sleep", lambda delay: None)
    with OpenAI(base_url=settings.main_base_url, api_key=settings.main_key, max_retries=0,
                http_client=httpx.Client(transport=PolicyRouter(settings, httpx.MockTransport(upstream)))) as client:
        assert cli.chat(client, settings.main_model, [{"role": "user", "content": "Hello"}], retries=2) == "Agenda updated."
    assert len(calls) == 2 and calls[0] == calls[1]


@pytest.mark.parametrize("kind", ["policy", "jev", "rate_limit"])
def test_scenario_never_retries_policy_evaluator_or_quota_failure(monkeypatch, kind):
    from openai import OpenAI, APIStatusError
    from stopslop_demo import cli
    calls = []
    statuses = {"policy": (403, "policy_blocked"), "jev": (503, "jev_unavailable"),
                "rate_limit": (429, "rate_limit")}
    def upstream(request):
        calls.append(request)
        status, code = statuses[kind]
        return httpx.Response(status, json={"error": {"code": code, "message": code}})
    def sleep(delay):
        pytest.fail("Unexpected retry")
    monkeypatch.setattr(cli.time, "sleep", sleep)
    settings = config()
    with OpenAI(base_url=settings.main_base_url, api_key=settings.main_key, max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(upstream))) as client:
        with pytest.raises(APIStatusError):
            cli.chat(client, settings.main_model, [{"role": "user", "content": "Hello"}], retries=2)
    assert len(calls) == 1


def test_jev_questions_contain_explicit_targets_even_with_long_history():
    from stopslop.jev import JevEvaluator
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={"answers": {name: {"type": "noul", "noul": .01}
                                                     for name in body["questions"]}})
    messages = [{"role": "user", "content": f"Generic message {index}"} for index in range(12)]
    rule = {"id": "nda", "description": "Do not disclose protected designs", "threshold": 75}
    JevEvaluator(config(), httpx.MockTransport(respond)).assess(messages, [rule])
    for index, message in enumerate(messages):
        question = requests[0]["questions"][f"m{index}_nda"]["instructions"]
        assert question["target_message"] == message
        assert question["target_index"] == index
        assert question["policy"] == rule["description"]
