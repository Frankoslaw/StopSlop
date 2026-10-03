import json
import pytest
from stopslop.runtime import Runtime, validate_budgets
from stopslop.policy import PolicyError


def runtime(tmp_path, kind="fixed_quota", tokens="output", limit=10):
    now = [100.0]
    budget = dict(id="budget", type=kind, tokens=tokens, limit=limit, window_seconds=10)
    if kind == "fixed_quota":
        budget["reset_at"] = "1970-01-01T00:00:00+00:00"
    validate_budgets([budget])
    return Runtime([budget], lambda: now[0], state_file=tmp_path / "state.sqlite3"), now


def payload(n=6):
    return {"model": "model", "messages": [{"content": "hi"}], "max_tokens": n}


def test_reservations_prevent_concurrent_overspend(tmp_path):
    r, now = runtime(tmp_path)
    ticket = r.reserve(payload())
    with pytest.raises(PolicyError) as error:
        r.reserve(payload())
    assert error.value.status == 429
    r.finish(ticket, {"usage": {"prompt_tokens": 2, "completion_tokens": 3}})
    r.reserve(payload())
    stats = r.snapshot()
    assert stats["output_tokens"] == 3
    assert stats["in_flight"] == 1


def test_fixed_reset_boundary(tmp_path):
    r, now = runtime(tmp_path)
    r.finish(r.reserve(payload(10)), {"usage": {"prompt_tokens": 2, "completion_tokens": 10}})
    now[0] = 109.99
    with pytest.raises(PolicyError):
        r.reserve(payload(1))
    now[0] = 110
    r.reserve(payload(10))


def test_rolling_average_and_expiry(tmp_path):
    r, now = runtime(tmp_path, "rolling_average", limit=1)
    r.finish(r.reserve(payload(10)), {"usage": {"prompt_tokens": 2, "completion_tokens": 10}})
    now[0] = 109
    with pytest.raises(PolicyError):
        r.reserve(payload(1))
    now[0] = 110
    r.reserve(payload(10))


def test_input_selector_and_failed_reservation(tmp_path):
    r, now = runtime(tmp_path, tokens="input", limit=18)
    ticket = r.reserve(payload(1000))
    with pytest.raises(PolicyError):
        r.reserve(payload())
    r.finish(ticket, failed=True)
    r.reserve(payload())
    r.violation("budget_exceeded", ["budget"])
    assert any(row.get("code") == "budget_exceeded" for row in r.repository.records("audit"))


@pytest.mark.parametrize("change", [{"limit": float("nan")}, {"tokens": "bad"}, {"window_seconds": 0}, {"reset_at": "2026-01-01"}])
def test_invalid_budget(change):
    b = dict(id="b", type="fixed_quota", tokens="total", limit=10, window_seconds=60, reset_at="2026-01-01T00:00:00Z")
    with pytest.raises(ValueError):
        validate_budgets([{**b, **change}])


def test_proxy_budget_metrics_and_model_forwarding(tmp_path):
    import asyncio
    import httpx
    from stopslop.config import Settings
    from stopslop.proxy import create_app
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": 1, "rules": [], "allowed_models": ["client-model"], "budgets": [
        dict(id="output", type="fixed_quota", tokens="output", limit=10,
             window_seconds=86400, reset_at="2026-01-01T00:00:00Z")]}))
    calls = []
    def backend(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 6}})
    app = create_app(Settings(main_key="test", deterministic=True, preserve_model=True,
                             policy_file=str(policy)), httpx.MockTransport(backend))
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as c:
            body = {"model": "client-model", "max_tokens": 6, "messages": [{"role": "user", "content": "hello"}]}
            assert (await c.post("/v1/chat/completions", json=body)).status_code == 200
            assert (await c.post("/v1/chat/completions", json=body)).status_code == 429
    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0]["model"] == "client-model"
    metrics = app.state.policy.runtime.snapshot()
    assert metrics["output_tokens"] == 6
    assert metrics["violation_count"] == 1
    assert metrics["in_flight"] == 0
