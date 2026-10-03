"""Validated, local per-rule policy configuration. Regex rules and optional Jev semantic rules."""
import json
import re
from importlib.resources import files
from pathlib import Path

from .rules import Detector

ACTIONS = {"allow", "filter", "local", "block"}


class PolicyFile:
    def __init__(self, path: str):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or set(data) - {"version", "default_action", "rules"}:
            raise ValueError("Invalid policy file fields")
        if type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("default_action", "block"), str) or data.get("default_action", "block") not in ACTIONS:
            raise ValueError("Policy requires version 1 and a valid default_action")
        entries = data.get("rules", [])
        if not isinstance(entries, list):
            raise ValueError("Policy rules must be an array")
        builtins = json.loads(files("stopslop").joinpath("default_rules.json").read_text(encoding="utf-8"))
        definitions = {rule["id"]: rule for rule in builtins}
        self.default_action = data.get("default_action", "block")
        self.actions = {}
        self.exceptions = {}
        self.semantic_rules = []
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) - {"id", "pattern", "validator", "action", "allowed_patterns", "description", "threshold"}:
                raise ValueError("Invalid policy rule fields")
            rule_id = entry.get("id")
            if not isinstance(rule_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", rule_id) or rule_id in self.actions:
                raise ValueError("Rule IDs must be unique lowercase identifiers")
            action = entry.get("action", self.default_action)
            if not isinstance(action, str) or action not in ACTIONS:
                raise ValueError(f"Invalid action for {rule_id}")
            if "description" in entry:
                description = entry["description"]
                threshold = entry.get("threshold", 80)
                if (rule_id in definitions or "pattern" in entry or "validator" in entry
                        or "allowed_patterns" in entry or not isinstance(description, str)
                        or not description.strip() or type(threshold) not in (int, float)
                        or not 0 <= threshold <= 100):
                    raise ValueError("Semantic rules require a new ID, description and threshold from 0 to 100")
                self.semantic_rules.append({"id": rule_id, "description": description, "threshold": threshold})
                self.actions[rule_id] = action
                continue
            if "threshold" in entry:
                raise ValueError("threshold requires a semantic description")
            if rule_id in definitions and ("pattern" in entry or "validator" in entry):
                raise ValueError("Built-in patterns cannot be overwritten; use a custom rule ID")
            if rule_id not in definitions:
                if not isinstance(entry.get("pattern"), str) or not entry["pattern"]:
                    raise ValueError("Custom rules require a nonempty regex pattern")
                definitions[rule_id] = {key: entry[key] for key in ("id", "pattern", "validator") if key in entry}
            allowed = entry.get("allowed_patterns", [])
            if not isinstance(allowed, list) or any(not isinstance(p, str) or not p for p in allowed):
                raise ValueError("allowed_patterns must be an array of nonempty regexes")
            try:
                self.exceptions[rule_id] = [re.compile(p, re.IGNORECASE) for p in allowed]
            except re.error as error:
                raise ValueError("Invalid allowed pattern") from error
            self.actions[rule_id] = action
        try:
            self.detector = Detector(definitions=list(definitions.values()))
        except (re.error, TypeError, KeyError) as error:
            raise ValueError("Invalid policy pattern") from error

    def scan(self, text, on_rule=None):
        # An exception applies only to a fully contained match of its own rule.
        return [hit for hit in self.detector.scan(text, on_rule)
                if not any(m.start() <= hit.start and hit.end <= m.end()
                           for pattern in self.exceptions.get(hit.rule_id, [])
                           for m in pattern.finditer(text))]

    def action_for(self, rule_id):
        return self.actions.get(rule_id, self.default_action)
