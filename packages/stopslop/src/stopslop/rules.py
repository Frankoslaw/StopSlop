from dataclasses import dataclass
from importlib.resources import files
import json
import regex as re

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
    if validator == "iban":
        compact = re.sub(r"\s", "", value).upper()
        lengths = {"PL": 28, "DE": 22, "GB": 22, "FR": 27, "ES": 24, "IT": 27,
                   "NL": 18, "BE": 16, "CH": 21, "AT": 20, "IE": 22, "PT": 25}
        if len(compact) != lengths.get(compact[:2]):
            return False
        rearranged = compact[4:] + compact[:4]
        return int("".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)) % 97 == 1
    if validator == "luhn":
        if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
            return False
        return sum((int(n) * 2 // 10 + int(n) * 2 % 10) if i % 2 else int(n)
                   for i, n in enumerate(reversed(digits))) % 10 == 0
    return True


class Detector:
    def __init__(self, rules_file: str = "", definitions: list | None = None):
        source = files("stopslop").joinpath("default_rules.json")
        if rules_file:
            from pathlib import Path
            source = Path(rules_file)
        if definitions is None:
            definitions = json.loads(source.read_text(encoding="utf-8"))
        self.rules = []
        ids = set()
        for rule in definitions:
            if rule["id"] in ids or rule.get("validator") not in (None, "pesel", "bank", "iban", "luhn"):
                raise ValueError("duplicate rule ID or unknown validator")
            ids.add(rule["id"])
            self.rules.append((rule["id"], re.compile(rule["pattern"], re.IGNORECASE), rule.get("validator")))

    def scan(self, text: str, on_rule=None) -> list[Match]:
        hits = []
        for rule_id, pattern, validator in self.rules:
            if on_rule:
                on_rule(rule_id)
            hits.extend(Match(rule_id, match.start(), match.end())
                        for match in pattern.finditer(text, timeout=0.05)
                        if valid_identifier(match.group(), validator))
        return hits

    def redact(self, text: str, matches: list[Match]) -> str:
        for start, end, rule_id in reversed(self.spans(matches)):
            text = text[:start] + f"[REDACTED:{rule_id}]" + text[end:]
        return text

    @staticmethod
    def spans(matches: list[Match]) -> list[tuple[int, int, str]]:
        # Merge overlapping spans so shorter matches cannot expose a larger identifier.
        spans = []
        for match in sorted(matches, key=lambda m: (m.start, -m.end)):
            if spans and match.start < spans[-1][1]:
                spans[-1] = (spans[-1][0], max(spans[-1][1], match.end), spans[-1][2])
            else:
                spans.append((match.start, match.end, match.rule_id))
        return spans
