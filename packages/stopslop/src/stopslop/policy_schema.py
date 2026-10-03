"""Translate editable rule types into stable internal audit identifiers."""
from copy import deepcopy
import hashlib
import json
from importlib.resources import files

BUILTIN_TYPES = frozenset(rule["id"] for rule in json.loads(
    files("stopslop").joinpath("default_rules.json").read_text(encoding="utf-8")))


def normalize_policy(data):
    if not isinstance(data, dict):
        return data
    data = deepcopy(data)

    def rules(entries, output=False):
        if not isinstance(entries, list):
            return entries
        result = []
        for entry in entries:
            if not isinstance(entry, dict):
                result.append(entry)
                continue
            entry = dict(entry)
            if "type" in entry:
                if "id" in entry:
                    raise ValueError("Use type, not both type and id, for a rule")
                kind = entry.pop("type")
                if not isinstance(kind, str) or not kind:
                    raise ValueError("Rule type must be a nonempty string")
                if kind.startswith("builtin."):
                    kind = kind.removeprefix("builtin.")
                    if kind not in BUILTIN_TYPES:
                        raise ValueError("Unknown built-in rule type")
                if kind in ("regex", "semantic"):
                    required = "pattern" if kind == "regex" else "description"
                    # Output overrides can refer to an existing named generic rule.
                    if required not in entry and not (output and set(entry) == {"name", "action"}):
                        raise ValueError(f"{kind} rules require {required}")
                    if (kind == "regex" and "description" in entry) or (kind == "semantic" and "pattern" in entry):
                        raise ValueError("Rule type conflicts with its definition")
                    name = entry.pop("name", None)
                    if name is None:
                        content = entry.get(required)
                        if not isinstance(content, str) or not content:
                            raise ValueError("A generic rule requires a name or definition")
                        name = kind + "_" + hashlib.sha256(content.encode()).hexdigest()[:12]
                    entry["id"] = name
                else:
                    if "name" in entry:
                        raise ValueError("Named custom rules must use type regex or semantic")
                    entry["id"] = kind
            elif "name" in entry:
                raise ValueError("Rule name requires type regex or semantic")
            result.append(entry)
        return result

    if "rules" in data:
        data["rules"] = rules(data["rules"])
    if isinstance(data.get("output"), dict) and "rules" in data["output"]:
        data["output"]["rules"] = rules(data["output"]["rules"], output=True)
    if isinstance(data.get("budgets"), list):
        for entry in data["budgets"]:
            if isinstance(entry, dict) and "name" in entry:
                if "id" in entry:
                    raise ValueError("Use name, not both name and id, for a budget")
                entry["id"] = entry.pop("name")
    permissions = data.get("permissions")
    if isinstance(permissions, dict) and isinstance(permissions.get("rules"), list):
        for entry in permissions["rules"]:
            if not isinstance(entry, dict):
                continue
            for public, internal in (("name", "id"), ("type", "kind")):
                if public in entry:
                    if internal in entry:
                        raise ValueError(f"Use {public}, not both {public} and {internal}, for a permission")
                    entry[internal] = entry.pop(public)
    return data


def editable_policy(data):
    """Export canonical internal policies using the public type/name schema."""
    data = normalize_policy(data)
    for entries in (data.get("rules", []), data.get("output", {}).get("rules", [])):
        for entry in entries:
            rule_id = entry.pop("id")
            if rule_id in BUILTIN_TYPES:
                entry["type"] = "builtin." + rule_id
            elif "pattern" in entry or "description" in entry:
                entry.update(type="regex" if "pattern" in entry else "semantic", name=rule_id)
            else:
                entry["type"] = rule_id
            # Put the type and name before the editable definition.
            ordered = {k: entry[k] for k in ("type", "name") if k in entry}
            ordered.update(entry)
            entry.clear()
            entry.update(ordered)
    for entry in data.get("budgets", []):
        entry["name"] = entry.pop("id")
    for entry in data.get("permissions", {}).get("rules", []):
        entry["name"] = entry.pop("id")
        entry["type"] = entry.pop("kind")
    return data
