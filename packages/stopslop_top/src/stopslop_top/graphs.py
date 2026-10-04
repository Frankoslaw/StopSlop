"""Bounded live metric history and terminal area charts."""
from collections import deque
import time

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from stopslop.processes import process_alive


class Graphs:
    def __init__(self):
        self.samples = deque(maxlen=120)
        self.previous = None
        self.updated = None

    def sample(self, sessions, state, now=None):
        from .top import budget_usage
        now = time.monotonic() if now is None else now
        if self.updated is not None and now - self.updated < 1:
            return
        totals = {key: sum(s.get(key, 0) for s in sessions) for key in
                  ("requests", "input_tokens", "output_tokens", "completed", "failures", "total_latency_seconds")}
        elapsed = now - self.updated if self.updated is not None else 1
        delta = {key: max(0, value - self.previous.get(key, value)) if self.previous else 0
                 for key, value in totals.items()}
        active = [s for s in sessions if not s.get("closed") and process_alive(s.get("pid"))]
        scopes = {}
        for session in sorted(active, key=lambda s: s.get("updated_at", 0)):
            scopes[session.get("budget_scope", session.get("session_id", session.get("pid")))] = session
        quota = 0
        for session in scopes.values():
            snapshot = dict(session, usage_events=state.get("events", []),
                            reservations=list(state.get("pending", {}).values()))
            for budget in session.get("budgets", []):
                used, reserved, cap, _ = budget_usage(snapshot, budget, time.time())
                if cap > 0:
                    quota = max(quota, 100 * (used + reserved) / cap)
        finished = delta["completed"] + delta["failures"]
        self.samples.append((delta["requests"] / elapsed,
                             (delta["input_tokens"] + delta["output_tokens"]) / elapsed,
                             delta["total_latency_seconds"] / finished if finished else 0,
                             100 * delta["failures"] / finished if finished else 0,
                             sum(s.get("in_flight", 0) for s in active), quota))
        self.previous, self.updated = totals, now

    def render(self, width, height):
        columns = 2 if width >= 80 else 1
        chart_width = max(8, width // columns - 5)
        chart_height = max(2, min(8, height // (6 // columns) - 5))
        cards = []
        for index, (title, unit, color, floor) in enumerate((
            ("Requests", "/s", "cyan", 1), ("Token throughput", "tokens/s", "green", 1),
            ("Mean call latency", "s", "magenta", 1), ("Failed calls", "%", "red", 100),
            ("Pending calls", "calls", "yellow", 1), ("Highest quota usage", "%", "blue", 100),
        )):
            values = [sample[index] for sample in self.samples][-chart_width:]
            peak = max(values, default=0)
            scale = max(floor, peak)
            rows = []
            padded = [0] * (chart_width - len(values)) + values
            for row in reversed(range(chart_height)):
                cells = ""
                for value in padded:
                    fill = min(8, max(0, round((value / scale * chart_height - row) * 8)))
                    cells += " ▁▂▃▄▅▆▇█"[fill]
                rows.append(Text(cells, style=color, no_wrap=True))
            current = values[-1] if values else 0
            cards.append(Panel(Group(Text(f"{current:,.2f} {unit} · peak {peak:,.2f} · scale {scale:,.0f}", style=color),
                                           *rows), title=title, border_style=color))
        grid = Table.grid(expand=True, padding=(0, 1))
        for _ in range(columns):
            grid.add_column(ratio=1)
        for start in range(0, len(cards), columns):
            grid.add_row(*cards[start:start + columns])
        return Group(Text("Live history · latest 120 samples · ≥1s intervals · oldest → newest", style="dim"),
                     grid, Text("Rates start after the first sample. Idle latency / failure rate = 0. Quotas include reservations.", style="dim"))
