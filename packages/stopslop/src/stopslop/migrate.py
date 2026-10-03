"""Explicitly import legacy telemetry and feeds without deleting their sources."""
from datetime import datetime
import json
from pathlib import Path
import time

from .adapt import validate_incident
from .policy_format import read_policy
from .policy_loader import merge_policy


def import_legacy(repository, *, metrics_file=None, audit_file=None, incidents_file=None,
                  dynamic_policy_file=None, base_policy_file=None):
    imported = dict(sessions=0, audit=0, incidents=0, violations=0)
    with repository.transaction():
        if metrics_file:
            path = Path(metrics_file)
            directory = path.with_name(path.name + ".sessions")
            sources = sorted(directory.glob("*.json")) if directory.exists() else [path]
            for source in sources:
                value = json.loads(source.read_text(encoding="utf-8"))
                session_id = value.get("session_id", "legacy:" + source.name)
                repository.save_session(session_id, value)
                for violation in value.get("violations", []):
                    repository.append("violation", violation.get("time", time.time()), session_id, violation)
                    imported["violations"] += 1
                imported["sessions"] += 1
        for source, kind in ((audit_file, "audit"), (incidents_file, "incident")):
            if not source:
                continue
            with Path(source).open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    value = json.loads(line)
                    stamp = value.get("time", value.get("timestamp", time.time()))
                    stamp = datetime.fromisoformat(stamp).timestamp() if isinstance(stamp, str) else stamp
                    if kind == "incident":
                        value = validate_incident(value)
                    repository.append(kind, stamp, "legacy", value)
                    imported["incidents" if kind == "incident" else "audit"] += 1
        if dynamic_policy_file:
            if not base_policy_file:
                raise ValueError("Importing a dynamic policy requires --policy-file")
            overlay = read_policy(dynamic_policy_file)
            previous = repository.dynamic_policy()
            combined = dict(version=1, rules=previous.get("rules", []) + overlay.get("rules", []),
                            output=dict(rules=previous.get("output", {}).get("rules", [])
                                        + overlay.get("output", {}).get("rules", [])))
            merge_policy(read_policy(base_policy_file), combined)
            repository.save_dynamic_policy(combined)
    return imported
