import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest

from stopslop.config import Settings
from stopslop.policy import Policy, PolicyError
from stopslop.proxy import create_app
from stopslop.router import PolicyRouter
from stopslop.runtime import Runtime
from stopslop.evaluators import assessment


def configuration(tmp_path, **policy_fields):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": 1, "default_action": "filter", "rules": [
        {"id": "secret", "action": "block"}], **policy_fields}))
    return Settings(main_key="upstream-secret", policy_file=str(path), deterministic=True,
                    metrics_file=str(tmp_path / "metrics.json"), log_file=str(tmp_path / "audit.log"))


def payload(text="Hello", model=None):
    result = {"messages": [{"role": "user", "content": text}], "max_tokens": 10}
    if model:
        result["model"] = model
    return result


def completion(text, **message_fields):
    return {"choices": [{"message": {"content": text, **message_fields}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 3}}


def send(kind, settings, backend, requests, evaluator=None):
    if kind == "router":
        router = PolicyRouter(settings, httpx.MockTransport(backend), evaluator=evaluator)
        with httpx.Client(transport=router) as client:
            return [client.post("http://test/v1/chat/completions", json=body, headers=headers)
                    for body, headers in requests], router.policy
    app = create_app(settings, httpx.MockTransport(backend), evaluator=evaluator)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return [await client.post("/v1/chat/completions", json=body, headers=headers)
                    for body, headers in requests]
    return asyncio.run(run()), app.state.policy


@pytest.mark.parametrize("kind", ["router", "proxy"])
def test_inherited_output_block_withholds_content_and_accounts_usage(tmp_path, kind):
    settings = configuration(tmp_path)
    responses, policy = send(kind, settings, lambda _: httpx.Response(200, json=completion("password=leaked")), [(payload(), {})])
    assert responses[0].status_code == 403
    assert responses[0].json()["error"]["code"] == "output_blocked"
    assert "leaked" not in responses[0].text
    assert not policy.runtime.pending
    assert policy.runtime.stats["output_tokens"] == 3
    assert policy.runtime.stats["actions"] == {"output_blocked": 1}
    assert policy.runtime.stats["violation_count"] == 1


@pytest.mark.parametrize("kind", ["router", "proxy"])
@pytest.mark.parametrize("action", ["warn", "block", "block_device"])
def test_explicit_output_action_and_authenticated_suspension(tmp_path, kind, action):
    settings = replace(configuration(tmp_path, output={"rules": [
        {"id": "dangerous_reply", "pattern": "execute malicious code", "action": action}]}),
        access_tokens=json.dumps({"device-a": "token-a", "device-b": "token-b"}), state_file=str(tmp_path / "state.sqlite3"))
    calls = []
    def backend(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer upstream-secret"
        return httpx.Response(200, json=completion("execute malicious code" if len(calls) == 1 else "Safe"))
    requests = [(payload(), {"Authorization": "Bearer token-a"}),
                (payload(), {"Authorization": "Bearer token-a"}),
                (payload(), {"Authorization": "Bearer token-b"})]
    responses, policy = send(kind, settings, backend, requests)
    assert responses[0].status_code == (200 if action == "warn" else 403)
    assert responses[1].status_code == (403 if action == "block_device" else 200)
    assert responses[2].status_code == 200
    if action == "warn":
        assert responses[0].headers["X-StopSlop-Output-Action"] == "warn"
    if action == "block_device":
        assert responses[1].json()["error"]["code"] == "device_blocked"
        other = Policy(settings)
        with pytest.raises(PolicyError, match="device_blocked"):
            other.authenticate("Bearer token-a")
        other.runtime.reset_client("device-a")
        assert other.authenticate("Bearer token-a") == "device-a"
    assert "execute malicious code" not in (tmp_path / "audit.log").read_text()
    assert policy.runtime.stats["requests"] == 3


@pytest.mark.parametrize("kind", ["router", "proxy"])
def test_client_identity_cannot_be_spoofed_and_missing_credentials_rejected(tmp_path, kind):
    settings = replace(configuration(tmp_path), access_tokens='{"client":"known-token"}')
    def forbidden(_):
        pytest.fail("Unauthenticated input reached backend")
    responses, policy = send(kind, settings, forbidden, [(payload(), {"X-Client-ID": "client"}),
                                                        (payload(), {"Authorization": "Bearer wrong"})])
    assert [r.status_code for r in responses] == [401, 401]
    assert policy.runtime.stats["requests"] == 2


@pytest.mark.parametrize("kind", ["router", "proxy"])
def test_response_filtering_preserves_local_restoration(tmp_path, kind):
    settings = configuration(tmp_path)
    def backend(request):
        anonymous = json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json=completion(anonymous + " new@example.org"))
    responses, _ = send(kind, settings, backend, [(payload("old@example.org"), {})])
    text = responses[0].json()["choices"][0]["message"]["content"]
    assert "old@example.org" in text and "new@example.org" not in text
    assert "[REDACTED:email]" in text


@pytest.mark.parametrize("kind", ["router", "proxy"])
@pytest.mark.parametrize("message", [{"reasoning_content": "password=leaked"}, {"refusal": "password=leaked"},
                                     {"tool_calls": [{"function": {"name": "execute"}}]}])
def test_hidden_output_and_tools_cannot_bypass_inspection(tmp_path, kind, message):
    responses, _ = send(kind, configuration(tmp_path), lambda _: httpx.Response(200, json=completion("Safe", **message)), [(payload(), {})])
    assert responses[0].status_code in (403, 502)
    assert "leaked" not in responses[0].text and "execute" not in responses[0].text


@pytest.mark.parametrize("kind", ["router", "proxy"])
def test_all_upstream_error_bodies_are_sanitized(tmp_path, kind):
    responses, policy = send(kind, configuration(tmp_path), lambda _: httpx.Response(400, json={
        "error": "provider echoed password=leaked"}), [(payload(), {})])
    assert responses[0].status_code == 400 and "leaked" not in responses[0].text
    assert not policy.runtime.pending


def test_semantic_output_uses_conversation_context_but_only_new_matches(tmp_path):
    settings = replace(configuration(tmp_path, rules=[{"id": "nda", "description": "Do not reveal NDA material.",
                                                       "action": "block"}]), deterministic=False)
    class Evaluator:
        name, model = "test", "semantic-test"
        def assess(self, messages, rules):
            return assessment(messages, rules, {f"m{i}_{r['id']}": .95 if m["content"] == "Leaked design" else .01
                                                for i, m in enumerate(messages) for r in rules})
    responses, _ = send("router", settings, lambda _: httpx.Response(200, json=completion("Leaked design")),
                        [(payload(), {})], Evaluator())
    assert responses[0].status_code == 403
    assert responses[0].json()["error"]["risks"] == {"m1_nda": 95}


def test_model_allowlist_applies_to_client_and_policy_routes(tmp_path):
    settings = replace(configuration(tmp_path, allowed_models=["approved"]), preserve_model=True, main_model="approved")
    policy = Policy(settings)
    with pytest.raises(PolicyError, match="model_not_allowed"):
        policy.route(payload(model="expensive-unapproved"))
    assert policy.route(payload(model="approved")).payload["model"] == "approved"
    local = Policy(replace(configuration(tmp_path, allowed_models=["approved"], rules=[
        {"id": "profanity", "action": "local"}]), local_model="unapproved-local"))
    with pytest.raises(PolicyError, match="model_not_allowed"):
        local.route(payload("shit"))


@pytest.mark.parametrize("kind", ["fixed_quota", "rolling_average"])
def test_pending_budget_cannot_age_out_and_completion_is_charged_now(tmp_path, kind):
    now = [100.0]
    budget = dict(id="b", type=kind, tokens="output", limit=10 if kind == "fixed_quota" else 1, window_seconds=10)
    if kind == "fixed_quota":
        budget["reset_at"] = "1970-01-01T00:00:00Z"
    runtime = Runtime(None, [budget], lambda: now[0], log_file=None)
    ticket = runtime.reserve(payload())
    now[0] = 120
    with pytest.raises(PolicyError, match="budget_exceeded"):
        runtime.reserve(payload())
    runtime.finish(ticket, completion("Safe"))
    assert runtime.events[0][0] == 120
    with pytest.raises(PolicyError, match="budget_exceeded"):
        runtime.reserve(payload())
    now[0] = 130
    runtime.reserve(payload())


def test_persistent_shared_admission_is_atomic_and_survives_restart(tmp_path):
    budget = dict(id="b", type="fixed_quota", tokens="output", limit=10, window_seconds=86400,
                  reset_at="1970-01-01T00:00:00Z")
    path = str(tmp_path / "state.sqlite3")
    first, second = [Runtime(None, [budget], lambda: 100, log_file=None, state_file=path) for _ in range(2)]
    def reserve(runtime):
        try:
            return runtime.reserve(payload())
        except PolicyError:
            return None
    with ThreadPoolExecutor(2) as pool:
        tickets = list(pool.map(reserve, [first, second]))
    assert sum(t is not None for t in tickets) == 1
    restarted = Runtime(None, [budget], lambda: 100, log_file=None, state_file=path)
    with pytest.raises(PolicyError):
        restarted.reserve(payload())
    restarted.finish(next(t for t in tickets if t), completion("Safe"))
    with pytest.raises(PolicyError):
        first.reserve(payload())


def test_admission_across_separate_processes(tmp_path):
    import subprocess
    import sys
    code = """
import sys
from stopslop.runtime import Runtime
from stopslop.policy import PolicyError
budget = dict(id='b',type='fixed_quota',tokens='output',limit=10,window_seconds=86400,reset_at='1970-01-01T00:00:00Z')
runtime = Runtime(None,[budget],lambda:100,log_file=None,state_file=sys.argv[1])
try:
    runtime.reserve({'model':'m','messages':[{'content':'hi'}],'max_tokens':10})
    print('accepted')
except PolicyError:
    print('blocked')
runtime.close()
"""
    state = str(tmp_path / "process-state.sqlite3")
    def run(_):
        return subprocess.run([sys.executable, "-B", "-c", code, state], capture_output=True, text=True,
                              check=True, timeout=30).stdout.strip()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, range(2)))
    assert sorted(results) == ["accepted", "blocked"]


def test_orphan_release_refuses_live_owner(tmp_path):
    runtime = Runtime(None, log_file=None, state_file=str(tmp_path / "state.sqlite3"))
    ticket = runtime.reserve(payload())
    with pytest.raises(ValueError, match="owner is alive"):
        runtime.release_orphan(ticket)
    assert ticket in runtime.pending


def test_missing_model_list_defaults_to_configured_main(tmp_path):
    policy = Policy(replace(configuration(tmp_path), preserve_model=True))
    with pytest.raises(PolicyError, match="model_not_allowed"):
        policy.route(payload(model="unexpected-model"))


def test_output_only_semantic_rule_and_targeted_context(tmp_path):
    from stopslop.jev import JevEvaluator
    settings = replace(configuration(tmp_path, output={"inherit_input": False, "rules": [
        {"id": "output_nda", "description": "Block disclosure of secret designs.", "action": "block"}]}),
                       deterministic=False, classifier="jev", jev_key="mock")
    calls = []
    def classifier(request):
        body = json.loads(request.content)
        calls.append(body)
        assert list(body["questions"]) == ["m1_output_nda"]
        assert len(body["state"]["messages"]) == 2
        return httpx.Response(200, json={"answers": {"m1_output_nda": {"type": "noul", "noul": .99}}})
    evaluator = JevEvaluator(settings, httpx.MockTransport(classifier))
    responses, _ = send("router", settings, lambda _: httpx.Response(200, json=completion("Secret designs")),
                        [(payload(), {})], evaluator)
    assert responses[0].status_code == 403 and len(calls) == 1


def test_output_evaluator_failure_keeps_chat_charge(tmp_path):
    from stopslop.evaluators import EvaluationError
    settings = replace(configuration(tmp_path, output={"inherit_input": False, "rules": [
        {"id": "output_nda", "description": "Block disclosure of secret designs.", "action": "block"}]}), deterministic=False)
    class Broken:
        name, model = "broken", "model"
        def assess(self, *args):
            raise EvaluationError("private provider error")
    responses, policy = send("router", settings, lambda _: httpx.Response(200, json=completion("Secret designs")),
                             [(payload(), {})], Broken())
    assert responses[0].status_code == 503 and "private provider error" not in responses[0].text
    assert policy.runtime.stats["output_tokens"] == 3 and not policy.runtime.pending


def test_live_policy_snapshot_is_kept_for_inflight_output(tmp_path):
    settings = configuration(tmp_path)
    policy = Policy(settings)
    admitted = policy.route(payload())
    path = tmp_path / "policy.json"
    data = json.loads(path.read_text())
    data["output"] = {"inherit_input": False}
    path.write_text(json.dumps(data))
    newer = policy.route(payload())
    with pytest.raises(PolicyError, match="output_blocked"):
        policy.inspect_output(admitted, completion("password=leaked"))
    assert policy.inspect_output(newer, completion("password=leaked"))[1] == "allow"


def test_cancelled_admission_releases_reservation_before_forwarding(tmp_path):
    from threading import Event
    started, release = Event(), Event()
    app = create_app(configuration(tmp_path), httpx.MockTransport(lambda _: pytest.fail("Cancelled request was forwarded")))
    original = app.state.policy.route
    def delayed(*args):
        route = original(*args)
        started.set()
        release.wait(5)
        return route
    app.state.policy.route = delayed
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            task = asyncio.create_task(client.post("/v1/chat/completions", json=payload()))
            assert await asyncio.to_thread(started.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            for _ in range(100):
                if not app.state.policy.runtime.pending:
                    break
                await asyncio.sleep(.01)
            assert not app.state.policy.runtime.pending
    try:
        asyncio.run(run())
    finally:
        release.set()


@pytest.mark.parametrize("output", [{"default_action": []}, {"rules": [{"id": [], "action": "block"}]},
                                    {"rules": [{"id": "secret", "action": {}}]}, {"inherit_input": 1}])
def test_invalid_output_configuration_is_rejected(tmp_path, output):
    with pytest.raises(ValueError):
        Policy(configuration(tmp_path, output=output))


def test_policy_reload_applies_new_rules_and_invalid_reload_fails_closed(tmp_path):
    settings = configuration(tmp_path, rules=[])
    policy = Policy(settings)
    assert policy.route(payload("Project Raven")).action == "allow"
    data = json.loads((tmp_path / "policy.json").read_text())
    data["rules"].append({"id": "raven", "pattern": "Project Raven", "action": "block"})
    (tmp_path / "policy.json").write_text(json.dumps(data))
    with pytest.raises(PolicyError, match="policy_blocked"):
        policy.route(payload("Project Raven"))
    (tmp_path / "policy.json").write_text("{")
    with pytest.raises(PolicyError, match="invalid_policy_configuration"):
        policy.route(payload())


def test_semantic_assessment_is_budgeted_before_call(tmp_path):
    settings = replace(configuration(tmp_path, rules=[{"id": "nda", "description": "Do not reveal NDA material."}],
                       budgets=[dict(id="assessment", type="rolling_average", tokens="output", limit=1, window_seconds=10)]),
                       deterministic=False)
    class Forbidden:
        name, model = "test", "semantic-test"
        def assess(self, *args):
            pytest.fail("Over-budget assessment was called")
    with pytest.raises(PolicyError, match="budget_exceeded"):
        Policy(settings, evaluator=Forbidden()).route(payload())
