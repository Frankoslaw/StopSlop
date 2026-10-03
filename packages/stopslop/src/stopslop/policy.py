from copy import deepcopy
from dataclasses import dataclass, field, replace
import logging
import re
from .config import Settings
from .runtime import Runtime
from .rules import Detector
from .jev import JevEvaluator
from .evaluators import EvaluationError, SemanticEvaluator
from .policy_file import PolicyFile

logger = logging.getLogger("stopslop.audit")

class PolicyError(ValueError):
    def __init__(self, code: str, status: int, rules=(), risks=None):
        super().__init__(code)
        self.code, self.status, self.rules = code, status, list(rules)
        self.risks = risks or {}

@dataclass(frozen=True)
class Route:
    payload: dict
    base_url: str
    key: str
    action: str
    rules: list[str]
    replacements: dict[str, str] = field(default_factory=dict, repr=False)

    risks: dict[str, float] = field(default_factory=dict)
    ticket: str = ""

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
    def __init__(self, settings: Settings, jev_transport=None, evaluator: SemanticEvaluator | None = None):
        self.settings = settings
        self.detector = Detector(settings.rules_file)
        self.definition = PolicyFile(settings.policy_file) if settings.policy_file else None
        if self.definition:
            if settings.rules_file:
                raise ValueError("Use policy_file or rules_file, not both")
            self.detector = self.definition.detector
        self.runtime = Runtime(settings.metrics_file, self.definition.budgets if self.definition else [], log_file=settings.log_file)
        self.evaluator = None if settings.deterministic else (evaluator if evaluator is not None else JevEvaluator(settings, jev_transport))
        if (not settings.deterministic and evaluator is None and self.definition
                and self.definition.semantic_rules and not settings.jev_key):
            raise ValueError("Semantic policies require STOPSLOP_JEV_KEY")

    def route(self, payload) -> Route:
        try:
            route = self._route(payload)
            ticket = self.runtime.reserve(route.payload)
            if self.runtime.budgets:
                route.payload.setdefault("max_tokens", 1024)
            return replace(route, ticket=ticket)
        except PolicyError as error:
            self.runtime.violation(error.code, error.rules)
            raise

    def _route(self, payload) -> Route:
        allowed = {"model", "messages", "temperature", "top_p", "max_tokens", "stream", "reasoning_budget"}
        if not isinstance(payload, dict) or set(payload) - allowed or payload.get("stream", False) is not False:
            raise PolicyError("unsupported_payload", 400)
        if "reasoning_budget" in payload and (type(payload["reasoning_budget"]) is not int or not -1 <= payload["reasoning_budget"] <= 32768):
            raise PolicyError("invalid_reasoning_budget", 400)
        if "model" in payload and (not isinstance(payload["model"], str) or not payload["model"]):
            raise PolicyError("invalid_model", 400)
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or any(
            not isinstance(m, dict) or set(m) != {"role", "content"}
            or m["role"] not in ("system", "user", "assistant", "developer")
            or not isinstance(m["content"], str) for m in messages
        ):
            raise PolicyError("unsupported_messages", 400)
        semantic_rules = self.definition.semantic_rules if self.definition and self.evaluator else []
        total = len(self.detector.rules) * len(messages) + len(semantic_rules) * sum(bool(m["content"]) for m in messages)
        completed = 0

        def progress(rule_id, description=None, backend="deterministic"):
            nonlocal completed
            completed += 1
            description = description or "Check " + rule_id.replace("_", " ")
            logger.info("Running rule (%s/%s): %s", completed, total, description,
                        extra={"progress": f"Running rule ({completed}/{total}): {description} [{backend}]"})

        matches = [(self.definition.scan(m["content"], progress) if self.definition else self.detector.scan(m["content"], progress))
                   for m in messages]
        risks = {}
        # A deterministic block needs no remote assessment.
        blocked = self.definition and any(self.definition.action_for(hit.rule_id) == "block"
                                          for hits in matches for hit in hits)
        if self.definition and self.definition.semantic_rules and not blocked and self.evaluator:
            for message in messages:
                if message["content"]:
                    for rule in semantic_rules:
                        progress(rule["id"], rule["description"], self.evaluator.name)
            try:
                semantic, risks = self.evaluator.assess(messages, self.definition.semantic_rules)
            except EvaluationError:
                logger.error("backend=%s model=%s evaluation failed; chat backend not called", self.evaluator.name, self.evaluator.model)
                raise PolicyError(f"{self.evaluator.name}_unavailable", 503) from None
            for index, message in enumerate(messages):
                for rule in self.definition.semantic_rules:
                    name = f"m{index}_{rule['id']}"
                    if name not in risks:
                        continue
                    triggered = risks[name] >= rule["threshold"]
                    level = logging.ERROR if triggered and self.definition.action_for(rule["id"]) == "block" else logging.WARNING if triggered else logging.INFO
                    logger.log(level, "rule=%s message=%s backend=%s model=%s risk=%.1f%% threshold=%.1f%% result=%s action=%s",
                               rule["id"], index, self.evaluator.name, self.evaluator.model, risks[name], rule["threshold"],
                               "threshold reached" if triggered else "passed", self.definition.action_for(rule["id"]),
                               extra={"rule_check": True})
            matches = [hits + extra for hits, extra in zip(matches, semantic)]
        elif self.definition and self.definition.semantic_rules:
            logger.info("Semantic rules skipped: %s", "deterministic mode" if self.settings.deterministic else "deterministic block")
        rules = sorted({hit.rule_id for hits in matches for hit in hits})
        action = self.settings.policy if rules else "allow"
        if self.definition:
            actions = {self.definition.action_for(rule) for rule in rules}
            action = next((candidate for candidate in ("block", "local", "filter", "allow") if candidate in actions), "allow")
        threshold_details = []
        for rule in semantic_rules:
            rule_risks = [risk for name, risk in risks.items() if name.endswith("_" + rule["id"])]
            if rule_risks and max(rule_risks) >= rule["threshold"]:
                threshold_details.append(f"{rule['id']}: risk={max(rule_risks):.1f}% threshold={rule['threshold']:.1f}%")
        summary = f"action={action} rules={','.join(rules) or 'none'}"
        if threshold_details:
            summary += " | " + "; ".join(threshold_details)
        if rules and action != "block":
            self.runtime.violation("policy_triggered", rules)
        if action == "block":
            logger.error("%s | chat backend=none", summary)
            raise PolicyError("policy_blocked", 403, rules, risks)
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
        outbound["model"] = settings.fallback_model if fallback else (payload.get("model", settings.main_model) if settings.preserve_model else settings.main_model)
        if action == "local":
            if not settings.local_model:
                raise PolicyError("missing_local_model", 503, rules)
            base, key = settings.local_base_url, settings.local_key
            outbound["model"] = settings.local_model
            outbound.pop("reasoning_budget", None)
        if not key:
            raise PolicyError("missing_upstream_key", 503)
        backend = "local" if action == "local" else "fallback" if fallback else "main"
        logger.log(logging.WARNING if action in ("filter", "local", "redirect") else logging.INFO,
                   "%s | chat backend=%s model=%s", summary, backend, outbound["model"])
        logger.info("Generating response", extra={"progress": f"Generating response [{backend}: {outbound['model']}]"})
        return Route(outbound, base, key, action, rules, replacements, risks=risks)
