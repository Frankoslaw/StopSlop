"""Editable TOML policies; JSON remains readable for existing integrations."""
import json
from pathlib import Path
import tomllib
from .policy_schema import normalize_policy, editable_policy


def read_policy(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    return normalize_policy(tomllib.loads(text) if path.suffix.lower() == ".toml" else json.loads(text))


def policy_toml(data):
    data = editable_policy(data)
    lines = ["# StopSlop policy. Reloaded before each request.",
             "# Actions: allow, filter, local, block. Thresholds are percentages.", ""]

    def scalar(value):
        if isinstance(value, list):
            return "[" + ", ".join(scalar(item) for item in value) + "]"
        return json.dumps(value, ensure_ascii=False)

    def table(value, prefix=""):
        for key, item in value.items():
            if not isinstance(item, dict) and not (isinstance(item, list) and item and isinstance(item[0], dict)):
                if key == "pattern" and isinstance(item, str) and "'" not in item and "\n" not in item and "\r" not in item:
                    lines.append(f"{key} = '{item}'")
                else:
                    lines.append(f"{key} = {scalar(item)}")
        for key, item in value.items():
            name = prefix + key
            if isinstance(item, dict):
                lines.extend(["", f"[{name}]"])
                table(item, name + ".")
            elif isinstance(item, list) and item and isinstance(item[0], dict):
                for entry in item:
                    lines.extend(["", f"[[{name}]]"])
                    table(entry, name + ".")

    table(data)
    return "\n".join(lines) + "\n"
