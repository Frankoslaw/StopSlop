from copy import deepcopy
from dataclasses import dataclass
import logging
from .config import Settings
from .rules import Detector

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

class Policy:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.detector = Detector(settings.rules_file)

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
        matches = [self.detector.scan(m["content"]) for m in messages]
        rules = sorted({hit.rule_id for hits in matches for hit in hits})
        action = self.settings.policy if rules else "allow"
        logger.info("action=%s rules=%s", action, ",".join(rules))
        if action == "block":
            raise PolicyError("policy_blocked", 403, rules)
        outbound = deepcopy(payload)
        if action == "filter":
            for message, hits in zip(outbound["messages"], matches):
                message["content"] = self.detector.redact(message["content"], hits)
        fallback = action == "redirect"
        settings = self.settings
        base = settings.fallback_base_url if fallback else settings.main_base_url
        key = settings.fallback_key if fallback else settings.main_key
        outbound["model"] = settings.fallback_model if fallback else settings.main_model
        if not key:
            raise PolicyError("missing_upstream_key", 503)
        return Route(outbound, base, key, action, rules)
