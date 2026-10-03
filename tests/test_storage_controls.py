import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
from pathlib import Path
from threading import Event
import time

import httpx
import pytest
from rich.console import Console

from stopslop.config import Settings
from stopslop.evaluators import assessment
from stopslop.permissions import AgentGuard
from stopslop.policy import Policy, PolicyError
from stopslop.policy_format import policy_toml, read_policy
from stopslop.proxy import create_app
from stopslop.repository import SQLiteRepository
from stopslop.router import PolicyRouter
from stopslop.runtime import Runtime
from stopslop_top.viewer import Viewer


def budget(limit=10):
    return dict(id="cap", type="fixed_quota", tokens="output", limit=limit,
                window_seconds=86400, reset_at="1970-01-01T00:00:00Z")


def payload(text="Hello", maximum=10):
    return dict(model="chat", messages=[dict(role="user", content=text)], max_tokens=maximum)


def completion(text="Safe", tokens=10):
    return dict(choices=[dict(message=dict(role="assistant", content=text))],
                usage=dict(prompt_tokens=2, completion_tokens=tokens))


def configuration(tmp_path, **fields):
    path = tmp_path / "policy.toml"
    path.write_text(policy_toml(dict(version=1, default_action="filter", rules=[]) | fields), encoding="utf-8")
    return Settings(main_key="provider-key", deterministic=True, main_model="chat",
                    state_file=str(tmp_path / "state.sqlite3"), policy_file=str(path))


def test_failed_reported_usage_remains_charged_and_attributed(tmp_path):
    runtime = Runtime([budget()], state_file=tmp_path / "state.sqlite3", clock=lambda: 100)
    runtime.finish(runtime.reserve(payload(), client_id="agent"), completion(), failed=True)
    assert runtime.stats["output_tokens"] == 10
    assert runtime.stats["failures"] == 1 and runtime.stats["completed"] == 0
    record = runtime.repository.records("audit")[0]
    assert record["output_tokens"] == 10 and record["client_id"] == "agent"
    with pytest.raises(PolicyError, match="budget_exceeded"):
        runtime.reserve(payload())


def test_quota_history_survives_disable_resize_and_restart(tmp_path):
    path = tmp_path / "state.sqlite3"
    now = [100]
    runtime = Runtime([budget()], state_file=path, clock=lambda: now[0])
    runtime.finish(runtime.reserve(payload()), completion())
    runtime.budgets = []
    runtime.finish(runtime.reserve(payload()), completion(tokens=1))
    now[0] = 102
    runtime.budgets = [{**budget(), "window_seconds": 1}]
    runtime.finish(runtime.reserve(payload(maximum=1)), completion(tokens=1))
    runtime.close()
    runtime = Runtime([budget()], state_file=path, clock=lambda: 100)
    assert len(runtime.events) == 3
    with pytest.raises(PolicyError, match="budget_exceeded"):
        runtime.reserve(payload())


def test_policy_reload_cannot_replace_inflight_admission_budget(tmp_path):
    entered, release = Event(), Event()

    class Evaluator:
        name, model = "mock", "classifier"
        calls = 0

        def assess(self, messages, rules):
            self.calls += 1
            if self.calls == 1:
                entered.set()
                assert release.wait(5)
            return assessment(messages, rules, {f"m{i}_{r['id']}": 0 for i, m in enumerate(messages) for r in rules})

    limits = [{**budget(), "models": ["chat"]}]
    rules = [dict(id="semantic", description="Block confidential disclosures.", action="block", threshold=80)]
    settings = replace(configuration(tmp_path, budgets=limits, rules=rules), deterministic=False)
    policy = Policy(settings, evaluator=Evaluator())
    policy.runtime.finish(policy.runtime.reserve(payload()), completion(tokens=9))
    with ThreadPoolExecutor(1) as pool:
        old = pool.submit(policy.route, payload())
        assert entered.wait(5)
        Path(settings.policy_file).write_text(policy_toml(dict(version=1, default_action="filter", rules=rules,
                                                              budgets=[])), encoding="utf-8")
        newer = policy.route(payload(maximum=1))
        policy.finish(newer, completion(tokens=0))
        release.set()
        with pytest.raises(PolicyError, match="budget_exceeded"):
            old.result(timeout=5)


@pytest.mark.parametrize("kind", ["router", "proxy"])
@pytest.mark.parametrize("enabled", [False, True])
def test_chat_recording_toggle_and_only_delivered_output_saved(tmp_path, kind, enabled):
    settings = replace(configuration(tmp_path), log_chats=enabled)
    backend = httpx.MockTransport(lambda request: httpx.Response(200, json=completion("A useful answer")))
    if kind == "router":
        with httpx.Client(transport=PolicyRouter(settings, backend)) as client:
            assert client.post("http://test/v1/chat/completions", json=payload("A question")).status_code == 200
    else:
        async def run():
            app = create_app(settings, backend)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                assert (await client.post("/v1/chat/completions", json=payload("A question"))).status_code == 200
            app.state.policy.runtime.close()
        asyncio.run(run())
    repository = SQLiteRepository(settings.state_file)
    assert repository.count("chat") == int(enabled)
    if enabled:
        record = repository.records("chat")[0]
        assert record["messages"][0]["content"] == "A question"
        assert record["response"]["choices"][0]["message"]["content"] == "A useful answer"
        assert "provider-key" not in json.dumps(record)
    assert not list(tmp_path.glob("*.json")) and not list(tmp_path.glob("*.log"))
    repository.close()


def test_blocked_chat_visible_without_storing_withheld_reply(tmp_path):
    settings = replace(configuration(tmp_path, rules=[dict(id="secret", action="block")]), log_chats=True)
    router = PolicyRouter(settings, httpx.MockTransport(lambda _: httpx.Response(200, json=completion("api_key=hidden"))))
    with httpx.Client(transport=router) as client:
        assert client.post("http://test/v1/chat/completions", json=payload("api_key=input-secret")).status_code == 403
        assert client.post("http://test/v1/chat/completions", json=payload()).status_code == 403
    repository = SQLiteRepository(settings.state_file)
    records = repository.records("chat")
    assert len(records) == 2 and all(row["response"] is None for row in records)
    assert {row["status"] for row in records} == {"policy_blocked", "output_blocked"}


@pytest.mark.parametrize("kind", ["router", "proxy"])
def test_body_limits_and_authentication_precede_parsing(tmp_path, kind):
    settings = replace(configuration(tmp_path), max_request_bytes=128,
                       access_tokens=json.dumps({"agent": "client-token"}))
    calls = []
    backend = httpx.MockTransport(lambda request: calls.append(request))
    async def proxy():
        app = create_app(settings, backend)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            unauthorized = await client.post("/v1/chat/completions", content=b"x" * 1000)
            oversized = await client.post("/v1/chat/completions", content=b"x" * 1000,
                                          headers={"Authorization": "Bearer client-token"})
            return unauthorized, oversized
    if kind == "proxy":
        responses = asyncio.run(proxy())
    else:
        with httpx.Client(transport=PolicyRouter(settings, backend)) as client:
            responses = [client.post("http://test/v1/chat/completions", content=b"x" * 1000),
                         client.post("http://test/v1/chat/completions", content=b"x" * 1000,
                                     headers={"Authorization": "Bearer client-token"})]
    assert [r.status_code for r in responses] == [401, 413] and not calls


def test_regex_timeout_fails_closed(tmp_path):
    settings = configuration(tmp_path, rules=[dict(id="expensive", pattern="(a+)+$", action="block")])
    policy = Policy(settings)
    start = time.monotonic()
    with pytest.raises(PolicyError, match="rule_timeout"):
        policy.route(payload("a" * 100000 + "!"))
    assert time.monotonic() - start < 3


def test_repository_transaction_rollback_and_injection():
    repository = SQLiteRepository(":memory:")
    with pytest.raises(RuntimeError):
        with repository.transaction():
            repository.append("audit", 100, "test", {"event": "should_rollback"})
            raise RuntimeError()
    assert repository.count("audit") == 0
    policy = Policy(Settings(main_key="test", state_file=":memory:"), repository=repository)
    route = policy.route(payload())
    policy.finish(route, completion())
    policy.runtime.close()
    assert repository.count("audit") == 1  # Injected repository remains caller-owned.


def test_toml_round_trip_and_live_edit(tmp_path):
    data = dict(version=1, rules=[dict(id="project", pattern=r"\bProject Żaba\b", action="block")])
    path = tmp_path / "policy.toml"
    path.write_text(policy_toml(data), encoding="utf-8")
    assert read_policy(path) == data
    policy = Policy(Settings(main_key="test", deterministic=True, policy_file=str(path)))
    with pytest.raises(PolicyError, match="policy_blocked"):
        policy.route(payload("Project Żaba"))
    data["rules"][0]["action"] = "allow"
    path.write_text(policy_toml(data), encoding="utf-8")
    assert policy.route(payload("Project Żaba")).action == "allow"


def permissions():
    return dict(rules=[
        dict(id="search", kind="tool", resource="search", operations=["call"], clients=["agent"], action="allow"),
        dict(id="mcp", kind="mcp", resource="docs/search", operations=["call"], clients=["agent"], action="allow"),
        dict(id="memory", kind="memory", resource="project/*", operations=["read", "write"], clients=["agent"], action="allow"),
        dict(id="secrets", kind="memory", resource="project/secrets*", operations=["read", "write", "delete"], clients=["*"], action="deny")])


def test_tool_mcp_memory_guards_deny_before_callbacks(tmp_path):
    settings = replace(configuration(tmp_path, permissions=permissions()), access_tokens=json.dumps({"agent": "token"}))
    policy = Policy(settings)
    guard = AgentGuard("token", policy=policy)
    calls = []
    assert guard.call_tool("search", lambda: "found") == "found"
    assert guard.call_mcp("docs", "search", lambda: "document") == "document"
    memory = {"plan": "safe"}
    assert guard.read_memory("project", "plan", memory.get) == "safe"
    guard.write_memory("project", "plan", "updated", lambda key, value: memory.update({key: value}))
    assert memory["plan"] == "updated"
    for operation in (lambda: guard.call_tool("shell", lambda: calls.append(1)),
                      lambda: guard.call_mcp("docs", "execute", lambda: calls.append(1)),
                      lambda: guard.read_memory("project", "secrets", lambda key: calls.append(1)),
                      lambda: guard.delete_memory("project", "plan", lambda key: calls.append(1))):
        with pytest.raises(PolicyError, match="operation_denied"):
            operation()
    assert not calls
    with pytest.raises(PolicyError, match="invalid_operation"):
        guard.read_memory("project", "../secrets", memory.get)
    with pytest.raises(PolicyError, match="unauthorized_client"):
        AgentGuard("fake", policy=policy).call_tool("search", lambda: calls.append(1))
    policy.runtime.suspend_client("agent")
    with pytest.raises(PolicyError, match="device_blocked"):
        guard.call_tool("search", lambda: calls.append(1))


def test_remote_permission_api_cannot_spoof_identity(tmp_path):
    settings = replace(configuration(tmp_path, permissions=permissions()),
                       access_tokens=json.dumps({"agent": "token", "other": "other-token"}))
    async def run():
        app = create_app(settings)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            request = dict(kind="tool", resource="search", operation="call")
            assert (await client.post("/v1/authorize", json=request)).status_code == 401
            assert (await client.post("/v1/authorize", json=request, headers={"Authorization": "Bearer token"})).status_code == 200
            assert (await client.post("/v1/authorize", json=request, headers={"Authorization": "Bearer other-token"})).status_code == 403
            request["client_id"] = "agent"
            assert (await client.post("/v1/authorize", json=request, headers={"Authorization": "Bearer other-token"})).status_code == 400
    asyncio.run(run())


def test_viewer_can_reach_oldest_records_and_read_long_chats():
    repository = SQLiteRepository(":memory:")
    for index in range(1101):
        repository.append("violation", index, "test", dict(code=f"violation_{index}", rules=["rule"]))
    repository.append("chat", 1200, "test", dict(messages=[dict(role="user", content="long message\n" * 100)],
                                                response=completion("answer"), status="completed"))
    console = Console(file=io.StringIO(), width=100, height=25, record=True)
    viewer = Viewer()
    viewer.key("2")
    console.print(viewer.render(repository, console))
    assert viewer.total == 1101
    viewer.key("end")
    console.print(viewer.render(repository, console))
    assert viewer.visible[-1][1]["code"] == "violation_0"
    viewer.key("enter")
    assert viewer.detail["code"] == "violation_0"
    viewer.key("tab")
    assert viewer.tab == 2 and viewer.detail is None
    console.print(viewer.render(repository, console))
    viewer.key("enter")
    console.print(viewer.render(repository, console))
    viewer.key("end")
    assert viewer.detail_scroll > 0
    console.print(viewer.render(repository, console))
    assert "answer" in console.export_text()
    assert viewer.key("q") is False


@pytest.mark.parametrize("kind", ["router", "proxy"])
def test_provider_error_usage_blocks_next_request(tmp_path, kind):
    settings = configuration(tmp_path, budgets=[budget()])
    calls = []

    def backend(request):
        calls.append(request)
        return httpx.Response(500, json=dict(error="private provider details", usage=completion()["usage"]))

    async def proxy():
        app = create_app(settings, httpx.MockTransport(backend))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return [await client.post("/v1/chat/completions", json=payload()) for _ in range(2)]

    if kind == "proxy":
        responses = asyncio.run(proxy())
    else:
        with httpx.Client(transport=PolicyRouter(settings, httpx.MockTransport(backend))) as client:
            responses = [client.post("http://test/v1/chat/completions", json=payload()) for _ in range(2)]
    assert [r.status_code for r in responses] == [500, 429] and len(calls) == 1
    assert "private provider details" not in responses[0].text


@pytest.mark.parametrize("kind", ["router", "proxy"])
def test_oversized_output_withheld_and_reservation_charged(tmp_path, kind):
    settings = replace(configuration(tmp_path, budgets=[budget()]), max_response_bytes=128, log_chats=True)
    backend = httpx.MockTransport(lambda _: httpx.Response(200, json=completion("x" * 1000)))

    async def proxy():
        app = create_app(settings, backend)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            return await client.post("/v1/chat/completions", json=payload())

    if kind == "proxy":
        response = asyncio.run(proxy())
    else:
        with httpx.Client(transport=PolicyRouter(settings, backend)) as client:
            response = client.post("http://test/v1/chat/completions", json=payload())
    assert response.status_code == 502 and response.json()["error"]["code"] == "response_too_large"
    repository = SQLiteRepository(settings.state_file)
    assert not repository.load_state()["pending"]
    assert repository.load_state()["events"][0][3] == 10
    assert repository.records("chat")[0]["response"] is None


@pytest.mark.parametrize("usage", [dict(prompt_tokens=2), dict(total_tokens=10)])
def test_partial_failed_usage_keeps_missing_component_estimates(usage):
    runtime = Runtime([budget()], state_file=":memory:")
    runtime.finish(runtime.reserve(payload()), dict(usage=usage), failed=True)
    assert runtime.stats["output_tokens"] == 10
    with pytest.raises(PolicyError, match="budget_exceeded"):
        runtime.reserve(payload())


def test_remote_guard_failures_do_not_execute_callbacks():
    calls = []

    def backend(request):
        assert request.url.path == "/v1/authorize"
        assert request.headers["authorization"] == "Bearer client-token"
        assert json.loads(request.content) == dict(kind="tool", resource="shell", operation="call")
        return httpx.Response(403)

    guard = AgentGuard("client-token", base_url="https://gateway", transport=httpx.MockTransport(backend))
    with pytest.raises(PolicyError, match="operation_denied"):
        guard.call_tool("shell", lambda: calls.append(1))
    assert not calls


def test_chat_display_does_not_replay_terminal_escape_sequences():
    from stopslop_top.viewer import display_text
    assert "\x1b" not in display_text("Hello \x1b[2Jworld")
    assert "\\x1b" in display_text("Hello \x1b[2Jworld")


@pytest.mark.parametrize("options", [dict(temperature={"hidden": "private"}), dict(top_p="private"),
                                    dict(temperature=float("inf")), dict(top_p=-1)])
def test_numeric_generation_fields_cannot_carry_uninspected_text(options):
    policy = Policy(Settings(main_key="test", state_file=":memory:"))
    with pytest.raises(PolicyError):
        policy.route(payload() | options)
