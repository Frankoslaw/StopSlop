"""Validated, local per-rule policy configuration. Regex rules and optional semantic rules."""
import json
import regex as re
from importlib.resources import files

from .rules import Detector
from .runtime import validate_budgets
from .policy_format import read_policy
from .permissions import validate_permissions
from .policy_schema import normalize_policy

ACTIONS = {"allow", "filter", "local", "block"}


class PolicyFile:
    def __init__(self, path: str = "", *, data=None):
        if data is None:
            data = read_policy(path)
        data = normalize_policy(data)
        if not isinstance(data, dict) or set(data) - {"version", "default_action", "rules", "budgets", "allowed_models", "output", "permissions", "budget_fallback"}:
            raise ValueError("Invalid policy file fields")
        if type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("default_action", "block"), str) or data.get("default_action", "block") not in ACTIONS:
            raise ValueError("Policy requires version 1 and a valid default_action")
        self.permissions = validate_permissions(data.get("permissions", {}))
        self.budgets = validate_budgets(data.get("budgets", []))
        self.budget_fallback = data.get("budget_fallback")
        if self.budget_fallback is not None:
            fallback = self.budget_fallback
            if (not isinstance(fallback, dict) or set(fallback) - {"route"}
                    or fallback.get("route") not in ("local", "fallback")):
                raise ValueError("budget_fallback requires route local/fallback")
        if any(b.get("on_exhaustion") == "fallback" for b in self.budgets) and not self.budget_fallback:
            raise ValueError("on_exhaustion fallback requires a budget_fallback configuration")
        self.allowed_models = data.get("allowed_models")
        if "allowed_models" in data and (not isinstance(self.allowed_models, list) or not self.allowed_models
                or any(not isinstance(m, str) or not m for m in self.allowed_models)):
            raise ValueError("allowed_models must be a nonempty array of model names")
        output = data.get("output", {})
        if (not isinstance(output, dict) or set(output) - {"inherit_input", "default_action", "rules"}
                or type(output.get("inherit_input", True)) is not bool
                or not isinstance(output.get("default_action", "block"), str)
                or output.get("default_action", "block") not in {"block", "block_device", "warn"}
                or not isinstance(output.get("rules", []), list)):
            raise ValueError("Invalid output policy")
        self.output_inherit = output.get("inherit_input", True)
        self.output_default = output.get("default_action", "block")
        self.output_actions = {}
        output_entries = []
        for entry in output.get("rules", []):
            if (not isinstance(entry, dict) or not isinstance(entry.get("action", self.output_default), str)
                    or entry.get("action", self.output_default) not in {"block", "block_device", "warn", "filter", "allow"}
                    or not isinstance(entry.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]*", entry["id"])):
                raise ValueError("Invalid output rule action")
            if entry.get("id") in self.output_actions:
                raise ValueError("Duplicate output rule ID")
            self.output_actions[entry.get("id")] = entry.get("action", self.output_default)
            output_entries.append({**entry, "action": "block"})
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
        # Existing input rule IDs may be overridden on output without repeating their definition.
        inherited = {entry["id"]: entry for entry in entries}
        expanded = []
        for entry in output_entries:
            if set(entry) <= {"id", "action"} and entry.get("id") in inherited:
                entry = {**inherited[entry["id"]], "action": "block"}
            expanded.append(entry)
        self.output_definition = PolicyFile(data={"version": 1, "rules": expanded}) if output_entries else None
        if self.output_definition:
            self.output_definition.detector.rules = [rule for rule in self.output_definition.detector.rules
                                                     if rule[0] in self.output_actions]

    def scan(self, text, on_rule=None):
        # Scan each exception once per text, even when its rule has many hits.
        spans = {}
        result = []
        for hit in self.detector.scan(text, on_rule):
            if hit.rule_id not in spans:
                spans[hit.rule_id] = [(m.start(), m.end())
                                      for pattern in self.exceptions.get(hit.rule_id, [])
                                      for m in pattern.finditer(text, timeout=0.05)]
            if not any(start <= hit.start and hit.end <= end for start, end in spans[hit.rule_id]):
                result.append(hit)
        return result

    def action_for(self, rule_id):
        return self.actions.get(rule_id, self.default_action)
