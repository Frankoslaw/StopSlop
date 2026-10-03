"""Live, read-only terminal monitor for runtime snapshots."""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import Settings


def process_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied: process exists.
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def read_sessions(path):
    directory = path.with_name(path.name + ".sessions")
    paths = sorted(directory.glob("*.json")) if directory.exists() else ([path] if path.exists() else [])
    sessions, warnings = [], []
    for source in paths:
        try:
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("started_at"), (int, float)):
                raise ValueError("Invalid runtime snapshot")
            sessions.append(data)
        except (OSError, ValueError) as error:
            warnings.append(f"Cannot read {source.name}: {error}")
    return sessions, warnings


def budget_usage(session, budget, now):
    window = budget["window_seconds"]
    start = now - window
    fixed = budget["type"] == "fixed_quota"
    if fixed:
        anchor = datetime.fromisoformat(budget["reset_at"]).timestamp()
        start = anchor + math.floor((now - anchor) / window) * window
    ceiling = budget["limit"] if fixed else budget["limit"] * window
    def amount(event):
        return event[2] if budget.get("tokens") == "input" else event[3] if budget.get("tokens") == "output" else event[2] + event[3]
    def included(event):
        return (event[0] >= start if fixed else event[0] > start) and (
            not budget.get("models") or event[1] in budget["models"])
    used = sum(amount(e) for e in session.get("usage_events", []) if included(e))
    reserved = sum(amount(e) for e in session.get("reservations", []) if included(e))
    return used, reserved, ceiling, max(0, start + window - now) if fixed else None


def dashboard(sessions, warnings, now=None):
    now = time.time() if now is None else now
    active = [s for s in sessions if not s.get("closed") and process_alive(s.get("pid"))]
    summary = Table.grid(expand=True)
    for _ in range(4):
        summary.add_column(ratio=1)
    summary.add_row(f"[bold cyan]{len(active)}[/] running sessions",
                    f"[bold cyan]{sum(s.get('in_flight', 0) for s in active)}[/] pending calls",
                    f"[bold green]{sum(s.get('completed', 0) for s in sessions)}[/] completed",
                    f"[bold red]{sum(s.get('failures', 0) for s in sessions)}[/] failed")
    summary.add_row(f"{sum(s.get('input_tokens', 0) for s in sessions):,} input tokens",
                    f"{sum(s.get('output_tokens', 0) for s in sessions):,} output tokens",
                    f"{sum(s.get('violation_count', 0) for s in sessions)} violations",
                    f"{sum(s.get('requests', 0) for s in sessions)} requests")
    limits = Table(expand=True)
    for title in ("Session / limit", "Scope", "Used + reserved / cap", "Usage", "Window"):
        limits.add_column(title)
    alerts = list(warnings)
    for s in active:
        for b in s.get("budgets", []):
            used, reserved, cap, reset = budget_usage(s, b, now)
            ratio = (used + reserved) / cap
            color = "red" if ratio >= 1 else "yellow" if ratio >= .8 else "green"
            bars = min(20, max(0, int(ratio * 20)))
            limits.add_row(f"{s.get('pid')} / {b['id']}", b.get("tokens", "total") +
                           (" · " + ", ".join(b["models"]) if b.get("models") else " · all models"),
                           f"{used:,} + {reserved:,} / {cap:,.0f}",
                           Text("━" * bars + "─" * (20 - bars) + f" {ratio:.0%}", style=color),
                           f"reset in {reset:.0f}s" if reset is not None else f"rolling {b['window_seconds']:g}s")
            if ratio >= .8:
                alerts.append(f"PID {s.get('pid')}: {b['id']} at {ratio:.0%} (includes reservations)")
    if not limits.row_count:
        limits.add_row("No active quota limits", "", "", "", "")
    recent = Table(expand=True)
    for title in ("Time", "PID", "Violation", "Rules"):
        recent.add_column(title)
    events = sorted(((v, s.get("pid", "?")) for s in sessions for v in s.get("violations", [])),
                    key=lambda item: item[0]["time"], reverse=True)
    for event, pid in events[:8]:
        recent.add_row(datetime.fromtimestamp(event["time"]).strftime("%H:%M:%S"), str(pid),
                       Text(event["code"], style="yellow" if event["code"] == "policy_triggered" else "red"),
                       Text(", ".join(event.get("rules", []))))
    triggers = sum(v["code"] == "policy_triggered" for v, _ in events)
    if triggers:
        alerts.append(f"{triggers} policy trigger warnings recorded")
    if not sessions:
        alerts.append("Waiting for a demo or gateway to start")
    processes = Table(expand=True)
    for title in ("PID / session", "Status", "Pending", "Actions", "Mean latency"):
        processes.add_column(title)
    for s in sorted(sessions, key=lambda s: s["started_at"], reverse=True)[:8]:
        count = s.get("completed", 0) + s.get("failures", 0)
        processes.add_row(f"{s.get('pid', '?')} / {s.get('session_id', 'legacy')[:8]}",
                          "[green]running[/]" if s in active else "finished",
                          str(s.get("in_flight", 0)) if s in active else "0",
                          Text(", ".join(f"{k}: {v}" for k, v in s.get("actions", {}).items()) or "—"),
                          f"{s.get('total_latency_seconds', 0) / count:.2f}s" if count else "—")
    return Group(Panel(summary, title="[bold cyan]slopstop-top[/] · LIVE", subtitle="Ctrl+C to quit · totals include saved sessions"),
                 Panel(processes, title="Sessions · latest 8"), Panel(limits, title="Quota usage · active sessions"),
                 Panel(recent, title="Recent violations · latest 8"),
                 Panel(Text("\n".join(alerts) or "No warnings", style="yellow" if alerts else "green"), title="Warnings"))


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--metrics-file", help="Same metrics path used by the demo or gateway")
    parser.add_argument("--interval", type=float, default=.5, help="Refresh interval in seconds (default: 0.5)")
    parser.add_argument("--once", action="store_true", help="Print one snapshot and exit")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("--interval must be finite and positive")
    path = Path(args.metrics_file or Settings.load(args.env_file).metrics_file)
    console = Console()
    def render():
        return dashboard(*read_sessions(path))
    if args.once or not console.is_terminal:
        console.print(render())
        return
    try:
        with Live(render(), console=console, screen=True, auto_refresh=False) as live:
            while True:
                time.sleep(args.interval)
                live.update(render(), refresh=True)
    except KeyboardInterrupt:
        pass
