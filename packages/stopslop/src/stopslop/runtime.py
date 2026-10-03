"""Quota admission and telemetry through one transactional repository."""
import math
import os
import time
import uuid
import hashlib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import RLock
from .repository import SQLiteRepository
import structlog

log = structlog.get_logger("stopslop.audit")


def validate_budgets(entries):
    if not isinstance(entries, list):
        raise ValueError("budgets must be an array")
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"id", "type", "tokens", "limit", "window_seconds", "reset_at", "models", "on_exhaustion"}:
            raise ValueError("Invalid budget fields")
        if not isinstance(entry.get("id"), str) or not entry["id"] or entry["id"] in ids:
            raise ValueError("Budget IDs must be unique nonempty strings")
        ids.add(entry["id"])
        if entry.get("on_exhaustion", "block") not in ("block", "fallback"):
            raise ValueError("Budget on_exhaustion must be block or fallback")
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
    def __init__(self, budgets=(), clock=time.time, state_file="stopslop.sqlite3", repository=None):
        self.budgets = budgets
        self.clock = clock
        self.lock = RLock()
        self.events = []
        self.pending = {}
        self.own_pending = set()
        self.pending_owners = {}
        self.blocked_clients = set()
        self.state_file = str(state_file or ":memory:")
        self.repository = repository or SQLiteRepository(self.state_file)
        self.owns_repository = repository is None
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
        """Serialize admission and suspension across all users of the repository."""
        with self.lock, self.repository.transaction():
            data = self.repository.load_state()
            self.events = data["events"]
            self.pending = data["pending"]
            self.pending_owners = data.get("pending_owners", {})
            self.blocked_clients = set(data.get("blocked_clients", []))
            try:
                yield
            finally:
                self.repository.save_state(dict(events=self.events, pending=self.pending,
                                                pending_owners=self.pending_owners,
                                                blocked_clients=sorted(self.blocked_clients)))

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
        from .processes import process_alive
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

    def snapshot(self):
        return {**self.stats, "updated_at": self.clock(),
                "in_flight": len(self.own_pending.intersection(self.pending)),
                "session_id": self.session_id, "pid": os.getpid(), "closed": self.closed,
                "budgets": list(self.budgets), "usage_events": self.events,
                "reservations": list(self.pending.values()), "shared_state": self.state_file != ":memory:",
                "budget_scope": hashlib.sha256(str(Path(self.state_file).resolve()).encode()).hexdigest()[:12]
                                if self.state_file != ":memory:" else self.session_id,
                "blocked_clients": len(self.blocked_clients), "controls": self.controls}

    def write(self):
        self.repository.save_session(self.session_id, self.snapshot())

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.write()
            if self.owns_repository:
                self.repository.close()

    def audit(self, event, **details):
        self.repository.append("audit", self.clock(), self.session_id, dict(event=event, **details))

    def log_chat(self, messages, response=None, **details):
        # Authorization headers and replacement dictionaries never enter this API.
        self.repository.append("chat", self.clock(), self.session_id,
                               dict(messages=messages, response=response, **details))

    def violation(self, code, rules, **details):
        with self.lock:
            self.stats["violation_count"] += 1
            self.stats["violations"].append(dict(time=self.clock(), code=code, rules=list(rules), **details))
            self.stats["violations"] = self.stats["violations"][-1000:]
            self.repository.append("violation", self.clock(), self.session_id, dict(code=code, rules=list(rules), **details))
            self.audit("policy_violation", code=code, rules=list(rules), **details)
            console_details = {key: value for key, value in details.items() if key not in ("risks", "thresholds")}
            log.warning("policy_violation", code=code, rules=list(rules), **console_details)
            self.write()

    def reserve(self, payload, started_at=None, budgets=None, client_id=""):
        budgets = self.budgets if budgets is None else budgets
        from .policy import PolicyError
        with self.state():
            now = self.clock()
            # UTF-8 bytes are a conservative input estimate, not a tokenizer count.
            inp = sum(len(m["content"].encode("utf-8")) + 16 for m in payload["messages"])
            out = payload.get("max_tokens", 1024)
            if type(out) is not int or out <= 0:
                raise PolicyError("invalid_max_tokens", 400)
            model = payload.get("model")
            # Keep usage history when limits change or are temporarily disabled.
            for b in budgets:
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
            self.pending_owners[ticket] = {"pid": os.getpid(), "session_id": self.session_id, "client_id": client_id}
            self.write()
            return ticket

    def finish(self, ticket, body=None, action="allow", failed=False):
        with self.state():
            event = self.pending.pop(ticket, None)
            self.own_pending.discard(ticket)
            owner = self.pending_owners.pop(ticket, {})
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
            reported = isinstance(usage, dict) and any(type(usage.get(name)) is int and usage[name] >= 0
                       for name in ("prompt_tokens", "input_tokens", "completion_tokens", "output_tokens", "total_tokens"))
            # Partial reports retain estimates for missing components rather than treating them as free.
            inp = count("prompt_tokens", "input_tokens", default=0 if failed and not reported else inp)
            out = count("completion_tokens", "output_tokens", default=0 if failed and not reported else out)
            charged = not failed or reported
            if charged:
                self.events.append((self.clock(), model, inp, out))
                self.stats["input_tokens"] += inp
                self.stats["output_tokens"] += out
            if not failed:
                self.stats["assessment_calls" if action == "assessment" else "completed"] += 1
                if action == "assessment":
                    self.stats["estimated_assessment_input_tokens"] += inp
                    self.stats["estimated_assessment_output_tokens"] += out
                actions = self.stats["actions"]
                actions[action] = actions.get(action, 0) + 1
            else:
                self.stats["assessment_failures" if action == "assessment" else "failures"] += 1
            self.audit("request_finished", model=model, action=action, failed=failed,
                       client_id=owner.get("client_id", ""), ticket=ticket,
                       input_tokens=inp if charged else 0, output_tokens=out if charged else 0,
                       latency_seconds=latency)
            self.write()
