"""Scrollable views; navigation can be tested without a terminal."""
from datetime import datetime, timezone
import json

from rich.console import Group
from rich.panel import Panel
from rich.text import Text


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).astimezone().isoformat(timespec="seconds")


def display_text(value):
    """Never replay terminal escape/control sequences from logged content."""
    return "".join(char if char.isprintable() or char in "\n\t" else f"\\x{ord(char):02x}"
                   for char in str(value))


def chat_preview(record):
    for message in reversed(record.get("messages", [])):
        if isinstance(message, dict) and message.get("role") == "user":
            return " ".join(display_text(message.get("content", "")).split())[:120]
    return record.get("model", "")


class Viewer:
    tabs = ("Overview", "Violations", "Chats")

    def __init__(self):
        self.tab = 0
        self.position = [0, 0, 0]
        self.detail = None
        self.detail_scroll = 0
        self.total = 0
        self.page_size = 10
        self.visible = []
        self.detail_lines = 0

    def key(self, key):
        if key in ("q", "Q"):
            return False
        if key in ("tab", "backtab", "left", "right", "1", "2", "3"):
            self.tab = int(key) - 1 if key in ("1", "2", "3") else (self.tab + (-1 if key in ("left", "backtab") else 1)) % 3
            self.detail = None
        elif key == "escape":
            self.detail = None
        elif key == "enter" and self.tab and self.visible and self.detail is None:
            record = next((r for index, r in self.visible if index == self.position[self.tab]), None)
            if record:
                self.detail = record
                self.detail_scroll = 0
        elif key in ("up", "down", "pageup", "pagedown", "home", "end", "j", "k"):
            maximum = max(0, (self.detail_lines - self.page_size) if self.detail else self.total - 1)
            current = self.detail_scroll if self.detail else self.position[self.tab]
            delta = -self.page_size if key == "pageup" else self.page_size if key == "pagedown" else -1 if key in ("up", "k") else 1
            current = 0 if key == "home" else maximum if key == "end" else min(maximum, max(0, current + delta))
            if self.detail:
                self.detail_scroll = current
            else:
                self.position[self.tab] = current
        return True

    def render(self, repository, console):
        self.page_size = max(3, console.height - 8)
        width = max(20, console.width - 6)
        if self.detail:
            lines = self.detail_text(self.detail).wrap(console, width)
            self.detail_lines = len(lines)
            self.detail_scroll = min(self.detail_scroll, max(0, len(lines) - self.page_size))
            content = Group(*lines[self.detail_scroll:self.detail_scroll + self.page_size])
            title = f"{self.tabs[self.tab]} #{self.detail['id']} · {timestamp(self.detail['time'])}"
        elif self.tab == 0:
            from .top import dashboard
            sessions = repository.sessions() if repository else []
            if repository:
                state = repository.load_state()
                for session in sessions:
                    session.update(usage_events=state["events"], reservations=list(state["pending"].values()),
                                   blocked_clients=len(state.get("blocked_clients", [])))
            rendered = console.render_lines(dashboard(sessions, []), console.options.update(width=width), pad=False)
            self.total = len(rendered)
            start = min(self.position[0], max(0, self.total - self.page_size))
            content = Group(*(Text("".join(segment.text for segment in line)) for line in rendered[start:start + self.page_size]))
            title = f"Overview · lines {start + 1}–{min(self.total, start + self.page_size)} / {self.total}"
        else:
            kind = "violation" if self.tab == 1 else "chat"
            self.total = repository.count(kind) if repository else 0
            self.position[self.tab] = min(self.position[self.tab], max(0, self.total - 1))
            start = self.position[self.tab] // self.page_size * self.page_size
            records = repository.records(kind, self.page_size, start) if repository else []
            self.visible = list(enumerate(records, start))
            rows = []
            for index, record in self.visible:
                selected = index == self.position[self.tab]
                prefix = "› " if selected else "  "
                label = record.get("code", "") if kind == "violation" else record.get("status", "completed")
                suffix = ", ".join(record.get("rules", [])) if kind == "violation" else chat_preview(record)
                rows.append(Text(prefix + timestamp(record["time"]) + "  " + display_text(label) + "  " + display_text(suffix),
                                 style="bold cyan" if selected else "", overflow="ellipsis", no_wrap=True))
            content = Group(*rows) if rows else Text("No violations recorded." if kind == "violation"
                                                       else "No chats recorded. Start the gateway or demo with --log-chats.")
            title = f"{self.tabs[self.tab]} · {self.total} records · newest first"
        tabs = Text("   ".join(("[" + name + "]") if index == self.tab else name for index, name in enumerate(self.tabs)), style="bold cyan")
        return Group(tabs, Panel(content, title=Text(title), height=self.page_size + 2),
                     Text("Tab / 1–3: switch · ↑↓ / PgUp/PgDn / Home/End: scroll · Enter: read · Esc: back · Q: quit"))

    @staticmethod
    def detail_text(record):
        if "messages" not in record:
            return Text(json.dumps(record, indent=2, ensure_ascii=False))
        text = Text(f"{timestamp(record['time'])} · {record.get('model', 'unrouted')} · {record.get('status', '')}\n")
        text.append(f"Client: {record.get('client_id') or 'anonymous'} · Action: {record.get('action', '')}\n\n")
        for message in record.get("messages", []):
            if isinstance(message, dict):
                text.append(display_text(message.get("role", "message")).upper() + "\n", style="bold cyan")
                text.append(display_text(message.get("content", "")) + "\n\n")
        response = record.get("response")
        if isinstance(response, dict):
            for choice in response.get("choices", []):
                message = choice.get("message", {})
                for key in ("content", "reasoning_content", "refusal"):
                    if message.get(key):
                        text.append("ASSISTANT " + key + "\n", style="bold green")
                        text.append(display_text(message[key]) + "\n\n")
        else:
            text.append("No reply delivered.\n")
        return text
