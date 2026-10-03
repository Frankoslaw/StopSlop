"""Thread-safe process-wide budgets and atomic runtime snapshots."""
import logging
import json
import math
import os
import time
import uuid
import sqlite3
import hashlib
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
import structlog

log = structlog.get_logger("stopslop.audit")


def validate_budgets(entries):
    if not isinstance(entries, list):
        raise ValueError("budgets must be an array")
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"id", "type", "tokens", "limit", "window_seconds", "reset_at", "models"}:
            raise ValueError("Invalid budget fields")
        if not isinstance(entry.get("id"), str) or not entry["id"] or entry["id"] in ids:
            raise ValueError("Budget IDs must be unique nonempty strings")
        ids.add(entry["id"])
        if entry.get("type") not in ("rolling_average", "fixed_quota") or entry.get("tokens", "total") not in ("input", "output", "total"):
            raise ValueError("Invalid budget type or token selector")
        for name in ("limit", "window_seconds"):
            value = entry.get(name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"Budget {name} must be finite and positive")
        if "models" in entry and (not isinstance(entry["models"], list) or not entry["models"] or any(not isinstance(m, str) or not m for m in entry["models"])):
            raise ValueError("models must be a nonempty array of model names")
        if entry["type"] == "fixed_quota":
            if not isinstance(entry.get("reset_at"), str):
                raise ValueError("fixed_quota requires an ISO reset_at timestamp")
            anchor = datetime.fromisoformat(entry["reset_at"])
            if anchor.tzinfo is None:
                raise ValueError("reset_at requires a timezone")
        elif "reset_at" in entry:
            raise ValueError("reset_at only applies to fixed quotas")
    return entries


class Runtime:
    def __init__(self, path, budgets=(), clock=time.time, log_file="stopslop.log", state_file=""):
        self.path = Path(path) if path else None
        self.budgets = budgets
        self.clock = clock
        audit = logging.getLogger("stopslop.runtime." + uuid.uuid4().hex)
        audit.setLevel(logging.INFO)
        audit.propagate = False
        if log_file:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            audit.addHandler(handler)
        self.audit = structlog.wrap_logger(audit, processors=[structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()])
        self.audit_logger = audit
        self.lock = RLock()
        self.events = []
        self.pending = {}
        self.own_pending = set()
        self.pending_owners = {}
        self.blocked_clients = set()
        self.state_file = state_file
        if state_file:
            Path(state_file).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(state_file, timeout=30) as db:
                db.execute("CREATE TABLE IF NOT EXISTS control_state (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)")
        self.session_id = uuid.uuid4().hex
        self.closed = False
        self.stats = dict(started_at=clock(), requests=0, completed=0, failures=0,
                          input_tokens=0, output_tokens=0, violation_count=0, violations=[], actions={}, total_latency_seconds=0,
                          assessment_calls=0, assessment_failures=0, assessment_latency_seconds=0,
                          estimated_assessment_input_tokens=0, estimated_assessment_output_tokens=0)
        self.controls = []
        with self.state():
            self.write()

    @contextmanager
    def state(self):
        """SQLite serializes admission across workers and preserves outstanding work.

        Unfinished reservations survive crashes conservatively; no time-based release.
        """
        with self.lock:
            if not self.state_file:
                yield
                return
            with sqlite3.connect(self.state_file, timeout=30) as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT value FROM control_state WHERE id=1").fetchone()
                if row:
                    data = json.loads(row[0])
                    self.events = data["events"]
                    self.pending = data["pending"]
                    self.pending_owners = data.get("pending_owners", {})
                    self.blocked_clients = set(data.get("blocked_clients", []))
                try:
                    yield
                finally:
                    value = json.dumps(dict(events=self.events, pending=self.pending,
                                            pending_owners=self.pending_owners,
                                            blocked_clients=sorted(self.blocked_clients)))
                    db.execute("INSERT OR REPLACE INTO control_state VALUES (1, ?)", (value,))

    def authorize_client(self, client_id):
        from .policy import PolicyError
        with self.state():
            if client_id in self.blocked_clients:
                raise PolicyError("device_blocked", 403)

    def suspend_client(self, client_id):
        with self.state():
            self.blocked_clients.add(client_id)

    def reset_client(self, client_id):
        with self.state():
            self.blocked_clients.discard(client_id)

    def release_orphan(self, ticket):
        from .top import process_alive
        with self.state():
            if ticket not in self.pending:
                raise ValueError("Unknown reservation")
            owner = self.pending_owners.get(ticket, {})
            if not owner.get("pid") or process_alive(owner["pid"]):
                raise ValueError("Reservation owner is alive or unknown; refusing to release ongoing work")
            self.pending.pop(ticket)
            self.pending_owners.pop(ticket, None)
            self.write()

    def request_started(self):
        with self.lock:
            self.stats["requests"] += 1

    def write(self):
        if self.path:
            snapshot = {**self.stats, "updated_at": self.clock(), "in_flight": len(self.own_pending.intersection(self.pending)),
                        "session_id": self.session_id, "pid": os.getpid(), "closed": self.closed,
                        "budgets": list(self.budgets), "usage_events": self.events,
                        "reservations": list(self.pending.values()),
                        "shared_state": bool(self.state_file),
                        "budget_scope": hashlib.sha256(str(Path(self.state_file).resolve()).encode()).hexdigest()[:12] if self.state_file else self.session_id,
                        "blocked_clients": len(self.blocked_clients), "controls": self.controls}
            directory = self.path.with_name(self.path.name + ".sessions")
            directory.mkdir(parents=True, exist_ok=True)
            for target in (self.path, directory / (self.session_id + ".json")):
                temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
                temporary.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
                os.replace(temporary, target)

    def close(self):
        with self.lock:
            self.closed = True
            self.write()
            for handler in list(self.audit_logger.handlers):
                handler.close()
                self.audit_logger.removeHandler(handler)

    def violation(self, code, rules, **details):
        with self.lock:
            self.stats["violation_count"] += 1
            self.stats["violations"].append(dict(time=self.clock(), code=code, rules=list(rules), **details))
            self.stats["violations"] = self.stats["violations"][-1000:]
            self.audit.warning("policy_violation", code=code, rules=list(rules), **details)
            console_details = {key: value for key, value in details.items() if key not in ("risks", "thresholds")}
            log.warning("policy_violation", code=code, rules=list(rules), **console_details)
            self.write()

    def reserve(self, payload, started_at=None):
        from .policy import PolicyError
        with self.state():
            now = self.clock()
            # UTF-8 bytes are a conservative input estimate, not a tokenizer count.
            inp = sum(len(m["content"].encode("utf-8")) + 16 for m in payload["messages"])
            out = payload.get("max_tokens", 1024)
            if type(out) is not int or out <= 0:
                raise PolicyError("invalid_max_tokens", 400)
            model = payload.get("model")
            horizon = max((b["window_seconds"] for b in self.budgets), default=0)
            self.events = [e for e in self.events if e[0] > now - horizon]
            for b in self.budgets:
                if b.get("models") and model not in b["models"]:
                    continue
                window = b["window_seconds"]
                start = now - window
                if b["type"] == "fixed_quota":
                    anchor = datetime.fromisoformat(b["reset_at"]).timestamp()
                    start = anchor + math.floor((now - anchor) / window) * window
                def amount(e):
                    return e[2] if b.get("tokens") == "input" else e[3] if b.get("tokens") == "output" else e[2] + e[3]
                used = sum(amount(e) for e in self.events
                           if (e[0] >= start if b["type"] == "fixed_quota" else e[0] > start)
                           and (not b.get("models") or e[1] in b["models"]))
                # Outstanding work is charged in every window until it completes.
                used += sum(amount(e) for e in self.pending.values()
                            if not b.get("models") or e[1] in b["models"])
                ceiling = b["limit"] * window if b["type"] == "rolling_average" else b["limit"]
                if used + amount((now, model, inp, out)) > ceiling:
                    raise PolicyError("budget_exceeded", 429, [b["id"]])
            ticket = uuid.uuid4().hex
            self.pending[ticket] = (now if started_at is None else started_at, model, inp, out)
            self.own_pending.add(ticket)
            self.pending_owners[ticket] = {"pid": os.getpid(), "session_id": self.session_id}
            self.write()
            return ticket

    def finish(self, ticket, body=None, action="allow", failed=False):
        with self.state():
            event = self.pending.pop(ticket, None)
            self.own_pending.discard(ticket)
            self.pending_owners.pop(ticket, None)
            if event is None:
                return
            now, model, inp, out = event
            latency = max(0, self.clock() - now)
            self.stats["assessment_latency_seconds" if action == "assessment" else "total_latency_seconds"] += latency
            usage = body.get("usage", {}) if isinstance(body, dict) else {}
            def count(*names, default):
                for name in names:
                    value = usage.get(name) if isinstance(usage, dict) else None
                    if type(value) is int and value >= 0:
                        return value
                return default
            inp = count("prompt_tokens", "input_tokens", default=inp)
            out = count("completion_tokens", "output_tokens", default=out)
            if not failed:
                self.events.append((self.clock(), model, inp, out))
                horizon = max((b["window_seconds"] for b in self.budgets), default=0)
                self.events = [e for e in self.events if e[0] > self.clock() - horizon]
                self.stats["input_tokens"] += inp
                self.stats["output_tokens"] += out
                self.stats["assessment_calls" if action == "assessment" else "completed"] += 1
                if action == "assessment":
                    self.stats["estimated_assessment_input_tokens"] += inp
                    self.stats["estimated_assessment_output_tokens"] += out
                actions = self.stats["actions"]
                actions[action] = actions.get(action, 0) + 1
            else:
                self.stats["assessment_failures" if action == "assessment" else "failures"] += 1
            self.audit.info("request_finished", model=model, action=action, failed=failed,
                            input_tokens=inp if not failed else 0, output_tokens=out if not failed else 0,
                            latency_seconds=max(0, self.clock() - now))
            self.write()
