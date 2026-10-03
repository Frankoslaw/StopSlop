from dataclasses import dataclass
from importlib.resources import files
import json
import re

@dataclass(frozen=True)
class Match:
    rule_id: str
    start: int
    end: int


def valid_identifier(value: str, validator: str | None) -> bool:
    digits = re.sub(r"\D", "", value)
    if validator == "pesel":
        return len(digits) == 11 and sum(int(n)*w for n, w in zip(digits, (1,3,7,9,1,3,7,9,1,3,1))) % 10 == 0
    if validator == "bank":
        return len(digits) == 26 and int(digits[2:] + "2521" + digits[:2]) % 97 == 1
    return True


class Detector:
    def __init__(self, rules_file: str = ""):
        source = files("stopslop").joinpath("default_rules.json")
        if rules_file:
            from pathlib import Path
            source = Path(rules_file)
        definitions = json.loads(source.read_text(encoding="utf-8"))
        self.rules = []
        ids = set()
        for rule in definitions:
            if rule["id"] in ids or rule.get("validator") not in (None, "pesel", "bank"):
                raise ValueError("duplicate rule ID or unknown validator")
            ids.add(rule["id"])
            self.rules.append((rule["id"], re.compile(rule["pattern"], re.IGNORECASE), rule.get("validator")))

    def scan(self, text: str) -> list[Match]:
        return [Match(rule_id, m.start(), m.end())
                for rule_id, pattern, validator in self.rules
                for m in pattern.finditer(text)
                if valid_identifier(m.group(), validator)]

    def redact(self, text: str, matches: list[Match]) -> str:
        # Merge overlapping spans so shorter matches cannot expose a larger identifier.
        spans = []
        for match in sorted(matches, key=lambda m: (m.start, -m.end)):
            if spans and match.start < spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], match.end), spans[-1][2])
            else:
                spans.append((match.start, match.end, match.rule_id))
        for start, end, rule_id in reversed(spans):
            text = text[:start] + f"[REDACTED:{rule_id}]" + text[end:]
        return text
