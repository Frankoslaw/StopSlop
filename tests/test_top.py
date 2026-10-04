import json
import os
import time

from rich.console import Console

from stopslop.runtime import Runtime
from stopslop_top.top import budget_usage, dashboard, process_alive, read_sessions


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


def test_overview_preserves_styles_without_an_outer_card():
    from stopslop_top.viewer import Viewer
    console = Console(width=100, height=60, force_terminal=True)
    view = Viewer()
    rendered = view.render(None, console)
    # The overview body is a group, not an extra panel around the dashboard.
    from rich.console import Group
    assert isinstance(rendered.renderables[1], Group)
    segments = list(console.render(rendered))
    assert any(segment.style and segment.style.color and segment.style.color.name == "cyan" and "0" in segment.text for segment in segments)
    assert any(segment.style and segment.style.color and segment.style.color.name == "yellow" and "Waiting for a demo" in segment.text for segment in segments)
    console.height = 20
    view.render(None, console)
    assert view.total > view.page_size
    view.key("end")
    console.print(view.render(None, console))


def test_graph_history_rates_resets_and_bound():
    from stopslop_top.graphs import Graphs
    graphs = Graphs()
    session = dict(pid=os.getpid(), requests=10, input_tokens=100, completed=5,
                   failures=1, total_latency_seconds=12, in_flight=2)
    graphs.sample([session], {}, now=0)
    assert graphs.samples[-1] == (0, 0, 0, 0, 2, 0)
    session.update(requests=14, input_tokens=140, completed=8, failures=2,
                   total_latency_seconds=20)
    graphs.sample([session], {}, now=2)
    assert graphs.samples[-1] == (2, 20, 2, 25, 2, 0)
    graphs.sample([session], {}, now=2.5)
    assert len(graphs.samples) == 2
    graphs.sample([], {}, now=3)
    assert graphs.samples[-1] == (0, 0, 0, 0, 0, 0)
    for now in range(4, 140):
        graphs.sample([], {}, now=now)
    assert len(graphs.samples) == 120


def test_graph_tab_navigation_and_small_terminal():
    from stopslop_top.viewer import Viewer
    view = Viewer()
    view.key("4")
    assert view.tabs[view.tab] == "Graphs"
    for width, height in ((100, 40), (40, 20)):
        console = Console(width=width, height=height, record=True)
        console.print(view.render(None, console))
        assert "Requests" in console.export_text()
        view.key("end")
        console.print(view.render(None, console))
        assert "Highest quota usage" in console.export_text()
        view.key("home")
    view.key("enter")
    assert view.detail is None
    view.key("tab")
    assert view.tab == 0
    view.key("backtab")
    assert view.tab == 3


def test_graph_quota_includes_reservations_and_excludes_closed_sessions():
    from stopslop_top.graphs import Graphs
    graphs = Graphs()
    session = dict(pid=os.getpid(), session_id="active", budgets=[
        dict(type="rolling_average", limit=10, window_seconds=10)])
    state = dict(events=[[time.time(), "model", 20, 30]],
                 pending={"ticket": [0, "model", 10, 20]})
    graphs.sample([session], state, now=0)
    assert graphs.samples[-1][-1] == 80
    session["closed"] = True
    graphs.sample([session], state, now=1)
    assert graphs.samples[-1][-1] == 0
