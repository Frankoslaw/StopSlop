from copy import deepcopy
from dataclasses import dataclass, field
import logging
import re
from .config import Settings
from .rules import Detector
from .policy_file import PolicyFile

logger = logging.getLogger("stopslop.audit")

class PolicyError(ValueError):
    def __init__(self, code: str, status: int, rules=()):
        super().__init__(code)
        self.code, self.status, self.rules = code, status, list(rules)

@dataclass(frozen=True)
class Route:
    payload: dict
    base_url: str
    key: str
    action: str
    rules: list[str]
    replacements: dict[str, str] = field(default_factory=dict, repr=False)

    def restore(self, body):
        """Restore exact tokens locally, in one pass (never cascade replacements)."""
        if not self.replacements:
            return body
        pattern = re.compile("|".join(re.escape(token) for token in self.replacements))

        def visit(value):
            if isinstance(value, str):
                return pattern.sub(lambda match: self.replacements[match.group()], value)
            if isinstance(value, list):
                return [visit(item) for item in value]
            if isinstance(value, dict):
                return {key: visit(item) for key, item in value.items()}
            return value

        return visit(body)

class Policy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.detector = Detector(settings.rules_file)
        self.definition = PolicyFile(settings.policy_file) if settings.policy_file else None
        if self.definition:
            if settings.rules_file:
                raise ValueError("Use policy_file or rules_file, not both")
            self.detector = self.definition.detector

    def route(self, payload) -> Route:
        allowed = {"model", "messages", "temperature", "top_p", "max_tokens", "stream", "reasoning_budget"}
        if not isinstance(payload, dict) or set(payload) - allowed or payload.get("stream", False) is not False:
            raise PolicyError("unsupported_payload", 400)
        if "reasoning_budget" in payload and (type(payload["reasoning_budget"]) is not int or not -1 <= payload["reasoning_budget"] <= 32768):
            raise PolicyError("invalid_reasoning_budget", 400)
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or set(m) != {"role", "content"}
            or m["role"] not in ("system", "user", "assistant", "developer")
            or not isinstance(m["content"], str) for m in messages
        ):
            raise PolicyError("unsupported_messages", 400)
        matches = [(self.definition.scan(m["content"]) if self.definition else self.detector.scan(m["content"]))
                   for m in messages]
        rules = sorted({hit.rule_id for hits in matches for hit in hits})
        action = self.settings.policy if rules else "allow"
        if self.definition:
            actions = {self.definition.action_for(rule) for rule in rules}
            action = next((candidate for candidate in ("block", "local", "filter", "allow") if candidate in actions), "allow")
        logger.info("action=%s rules=%s", action, ",".join(rules))
        if action == "block":
            raise PolicyError("policy_blocked", 403, rules)
        outbound = deepcopy(payload)
        replacements = {}
        filter_matches = [[hit for hit in hits if not self.definition or self.definition.action_for(hit.rule_id) == "filter"]
                          for hits in matches]
        if action == "filter" or (action == "local" and any(filter_matches)):
            tokens = {}
            # Reserve a namespace absent from all input, including literal tokens.
            prefix = "ANON"
            while any(f"[{prefix}:" in m["content"] for m in messages):
                prefix += "_"
            for message, hits in zip(outbound["messages"], filter_matches):
                text = message["content"]
                for start, end, rule_id in reversed(self.detector.spans(hits)):
                    original = text[start:end]
                    if original not in tokens:
                        token = f"[{prefix}:{rule_id}:{len(tokens) + 1}]"
                        tokens[original] = token
                        replacements[token] = original
                    text = text[:start] + tokens[original] + text[end:]
                message["content"] = text
        fallback = action == "redirect"
        settings = self.settings
        base = settings.fallback_base_url if fallback else settings.main_base_url
        key = settings.fallback_key if fallback else settings.main_key
        outbound["model"] = settings.fallback_model if fallback else settings.main_model
        if action == "local":
            if not settings.local_model:
                raise PolicyError("missing_local_model", 503, rules)
            base, key = settings.local_base_url, settings.local_key
            outbound["model"] = settings.local_model
            outbound.pop("reasoning_budget", None)
        if not key:
            raise PolicyError("missing_upstream_key", 503)
        return Route(outbound, base, key, action, rules, replacements)
