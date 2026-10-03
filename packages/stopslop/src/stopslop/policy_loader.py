"""Read and validate base policy plus an additive incident-generated overlay."""
import json
from pathlib import Path
from threading import Lock

from .policy_file import PolicyFile


def merge_policy(base, dynamic):
    if (not isinstance(dynamic, dict) or set(dynamic) - {"version", "rules", "output"}
            or type(dynamic.get("version")) is not int or dynamic["version"] != 1):
        raise ValueError("Dynamic policy must contain version 1, rules and optional output rules")
    PolicyFile(data=dynamic)
    base_ids = {rule["id"] for rule in base.get("rules", [])}
    from importlib.resources import files
    base_ids.update(rule["id"] for rule in json.loads(files("stopslop").joinpath("default_rules.json").read_text(encoding="utf-8")))
    if any(rule["id"] in base_ids or rule.get("action", "block") != "block"
           or "allowed_patterns" in rule for rule in dynamic.get("rules", [])):
        raise ValueError("Dynamic input rules must be new restrictive rules")
    merged = {**base, "rules": base.get("rules", []) + dynamic.get("rules", [])}
    dyn_output = dynamic.get("output", {})
    if set(dyn_output) - {"rules"}:
        raise ValueError("Dynamic output may only add rules")
    base_output = base.get("output", {})
    output_ids = {rule["id"] for rule in base_output.get("rules", [])}
    if any(rule["id"] in output_ids or rule.get("action", "block") not in {"block", "block_device"}
           or "allowed_patterns" in rule for rule in dyn_output.get("rules", [])):
        raise ValueError("Dynamic output rules cannot weaken existing controls")
    merged["output"] = {**base_output, "rules": base_output.get("rules", []) + dyn_output.get("rules", [])}
    PolicyFile(data=merged)
    return merged


class PolicyLoader:
    def __init__(self, settings):
        if settings.dynamic_policy_file and not settings.policy_file:
            raise ValueError("dynamic_policy_file requires policy_file")
        self.paths = [Path(settings.policy_file)] if settings.policy_file else []
        if settings.dynamic_policy_file:
            self.paths.append(Path(settings.dynamic_policy_file))
        self.signature = None
        self.definition = None
        self.lock = Lock()

    def load(self):
        with self.lock:
            signature = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None for p in self.paths)
            if signature == self.signature:
                return self.definition
            if not self.paths:
                return None
            data = json.loads(self.paths[0].read_text(encoding="utf-8"))
            if len(self.paths) > 1 and self.paths[1].exists():
                data = merge_policy(data, json.loads(self.paths[1].read_text(encoding="utf-8")))
            definition = PolicyFile(data=data)
            self.definition, self.signature = definition, signature
            return definition
