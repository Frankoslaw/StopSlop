import json
import os

from rich.console import Console

from stopslop.runtime import Runtime
from stopslop.top import budget_usage, dashboard, process_alive, read_sessions


def test_concurrent_sessions_and_shutdown(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = Runtime(state_file=path)
    second = Runtime(state_file=path)
    ticket = first.reserve({"model": "test", "messages": [{"content": "hi"}], "max_tokens": 6})
    sessions, warnings = read_sessions(path)
    assert not warnings and len(sessions) == 2
    assert sum(s["in_flight"] for s in sessions) == 1
    assert process_alive(os.getpid())
    first.finish(ticket, {"usage": {"prompt_tokens": 2, "completion_tokens": 3}})
    first.close()
    sessions, _ = read_sessions(path)
    assert sum(s["closed"] for s in sessions) == 1
    assert sum(s["completed"] for s in sessions) == 1
    second.close()


def test_monitor_accounts_reservations_model_scope_and_idle_expiry():
    session = {"usage_events": [[100, "a", 2, 4], [100, "b", 20, 40]],
               "reservations": [[105, "a", 3, 6]]}
    budget = dict(type="rolling_average", tokens="output", models=["a"], limit=2, window_seconds=10)
    assert budget_usage(session, budget, 109) == (4, 6, 20, None)
    assert budget_usage(session, budget, 110) == (0, 6, 20, None)
    budget.update(type="fixed_quota", reset_at="1970-01-01T00:00:00Z", limit=20)
    assert budget_usage(session, budget, 109) == (4, 6, 20, 1)
    assert budget_usage(session, budget, 110) == (0, 6, 20, 10)


def test_waiting_and_unreadable_snapshots_render(tmp_path):
    path = tmp_path / "state.sqlite3"
    assert read_sessions(path) == ([], [])
    console = Console(width=100, record=True)
    console.print(dashboard([], []))
    assert "Waiting for a demo" in console.export_text()
    path.write_text("{")
    sessions, warnings = read_sessions(path)
    assert not sessions and len(warnings) == 1
    path.unlink()
    runtime = Runtime(state_file=path)
    runtime.close()
    sessions, warnings = read_sessions(path)
    console.print(dashboard(sessions, warnings))
    assert "finished" in console.export_text()
