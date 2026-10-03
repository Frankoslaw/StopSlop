from copy import deepcopy
from dataclasses import dataclass, field, replace
import logging
import math
import re
import json
import hmac
from threading import Lock
from .config import Settings
from .runtime import Runtime
from .rules import Detector
from .jev import JevEvaluator
from .laya import LayaEvaluator
from .evaluators import EvaluationError, SemanticEvaluator, LLMEvaluator
from .policy_loader import PolicyLoader

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
    client_id: str = ""
    definition: object = field(default=None, repr=False)
    detector: object = field(default=None, repr=False)
    evaluator: object = field(default=None, repr=False)
    budgets: tuple = field(default_factory=tuple, repr=False)
    original_messages: list = field(default_factory=list, repr=False)
    budget_fallback: str = ""

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
    def __init__(self, settings: Settings, jev_transport=None, evaluator: SemanticEvaluator | None = None, repository=None):
        try:
            self._initialize(settings, jev_transport, evaluator, repository)
        except BaseException:
            if hasattr(self, "runtime"):
                self.runtime.close()
            raise

    def _initialize(self, settings, jev_transport, evaluator, repository):
        self.settings = settings
        self.detector = Detector(settings.rules_file)
        self.runtime = Runtime(state_file=settings.state_file, repository=repository)
        self.loader = PolicyLoader(settings, self.runtime.repository)
        self.definition = self.loader.load()
        self.evaluator_lock = Lock()
        self.tokens = json.loads(settings.access_tokens) if settings.access_tokens else {}
        self.injected_evaluator = evaluator
        self.jev_transport = jev_transport
        self.evaluator_cache = {}
        if self.definition:
            if settings.rules_file:
                raise ValueError("Use policy_file or rules_file, not both")
            self.detector = self.definition.detector
        classifier = (self.definition.classifier if self.definition else None) or settings.classifier
        if settings.deterministic:
            self.evaluator = None
        elif evaluator is not None:
            self.evaluator = evaluator
        elif classifier == "laya":
            self.evaluator = LayaEvaluator(settings)
        elif classifier == "llm":
            self.evaluator = LLMEvaluator(settings)
        else:
            self.evaluator = JevEvaluator(settings, jev_transport)
        if (not settings.deterministic and evaluator is None and self.definition
                and classifier == "jev" and (self.definition.semantic_rules or
                    (self.definition.output_definition and self.definition.output_definition.semantic_rules)) and not settings.jev_key):
            raise ValueError("Semantic policies require STOPSLOP_JEV_KEY")
        self.evaluator_cache[classifier] = self.evaluator
        self.runtime.budgets = self.definition.budgets if self.definition else []
        self.update_controls(self.definition)
        self.runtime.write()

    def update_controls(self, definition):
        detector = definition.detector if definition else self.detector
        self.runtime.controls = [dict(id=rule_id, action=definition.action_for(rule_id) if definition else self.settings.policy,
                                      direction="input", enabled=True) for rule_id, _, _ in detector.rules]
        if definition:
            self.runtime.controls += [dict(id=r["id"], action=definition.action_for(r["id"]), direction="input",
                                           threshold=r["threshold"], enabled=not self.settings.deterministic)
                                      for r in definition.semantic_rules]
            semantic_ids = {r["id"] for r in definition.semantic_rules}
            if definition.output_definition:
                semantic_ids.update(r["id"] for r in definition.output_definition.semantic_rules)
            self.runtime.controls += [dict(id=rid, action=a, direction="output",
                                           enabled=not (self.settings.deterministic and rid in semantic_ids))
                                      for rid, a in definition.output_actions.items()]
            self.runtime.controls += [dict(id=rule["id"], action=rule["action"], direction=rule["kind"],
                                           enabled=True, resource=rule["resource"], clients=rule["clients"],
                                           operations=rule["operations"]) for rule in definition.permissions]

    def authenticate(self, authorization):
        if not self.tokens:
            return ""
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        for client_id, expected in self.tokens.items():
            if hmac.compare_digest(token.encode(), expected.encode()):
                self.runtime.authorize_client(client_id)
                return client_id
        raise PolicyError("unauthorized_client", 401)

    def record_incident(self, kind, rules, **details):
        self.runtime.repository.append("incident", self.runtime.clock(), self.runtime.session_id,
                                       dict(kind=kind, rules=list(rules), **details))

    def authorize_operation(self, client_id, kind, resource, operation):
        from .permissions import authorize_operation
        try:
            definition = self.loader.load()
        except (OSError, ValueError, TypeError, KeyError):
            raise PolicyError("invalid_policy_configuration", 503) from None
        return authorize_operation(self, definition, client_id, kind, resource, operation)

    def assess(self, evaluator, messages, rules, target_start=0, budgets=(), client_id=""):
        budgets = tuple(deepcopy(budgets))
        ticket = ""
        if budgets:
            from .jev import violation_questions
            supports_targets = hasattr(evaluator, "assess_targets")
            questions = violation_questions(messages, rules, target_start if supports_targets else 0)
            assessment_payload = {"model": evaluator.model, "messages": [
                {"content": json.dumps({"state": {"messages": messages}, "questions": questions}) + " " * 2048}],
                "max_tokens": max(2048, len(questions) * 128)}
            ticket = self.runtime.reserve(assessment_payload, budgets=budgets, client_id=client_id)
        try:
            result = (evaluator.assess_targets(messages, rules, target_start)
                      if target_start and hasattr(evaluator, "assess_targets") else evaluator.assess(messages, rules))
        except BaseException:
            if ticket:
                self.runtime.finish(ticket, action="assessment", failed=True)
            raise
        if ticket:
            self.runtime.finish(ticket, action="assessment")
        return result

    def finish(self, route, body=None, action=None, failed=False, response=None, status=None):
        action = action or ("budget_fallback" if route.budget_fallback else route.action)
        self.runtime.finish(route.ticket, body, action, failed)
        if self.settings.log_chats:
            self.runtime.log_chat(route.original_messages, response, client_id=route.client_id,
                                  model=route.payload["model"], action=action,
                                  status=status or ("failed" if failed else "completed"), ticket=route.ticket)

    def evaluator_for(self, classifier):
        if self.settings.deterministic:
            return None
        with self.evaluator_lock:
            if classifier not in self.evaluator_cache:
                self.evaluator_cache[classifier] = (LayaEvaluator(self.settings) if classifier == "laya"
                                                    else LLMEvaluator(self.settings) if classifier == "llm"
                                                    else JevEvaluator(self.settings, self.jev_transport))
            if classifier == "jev" and not self.settings.jev_key:
                raise PolicyError("missing_evaluator_key", 503)
            return self.evaluator_cache[classifier]

    @staticmethod
    def can_budget_fallback(error, definition):
        return (error.code == "budget_exceeded" and definition and definition.budget_fallback
                and any(b["id"] in error.rules and b.get("on_exhaustion", "block") == "fallback"
                        for b in definition.budgets))

    def fallback_route(self, route, definition, allow_same_model=False):
        target = definition.budget_fallback["route"]
        # A privacy rule requiring local execution must never become a cloud request.
        if route.action == "local" and target != "local":
            raise PolicyError("budget_fallback_not_allowed", 403)
        settings = self.settings
        if target == "local":
            base, model, key = settings.local_base_url, settings.local_model, settings.local_key
        else:
            base, model, key = settings.fallback_base_url, settings.fallback_model, settings.fallback_key
        if not all((base, model, key)):
            raise PolicyError("missing_budget_fallback", 503)
        if model == route.payload["model"] and not allow_same_model:
            raise PolicyError("budget_fallback_same_model", 429)
        if definition.allowed_models and model not in definition.allowed_models:
            raise PolicyError("model_not_allowed", 403)
        payload = deepcopy(route.payload)
        payload["model"] = model
        payload.pop("reasoning_budget", None)
        evaluator = route.evaluator
        if definition.budget_fallback.get("classifier"):
            evaluator = self.evaluator_for(definition.budget_fallback["classifier"])
        return replace(route, payload=payload, base_url=base, key=key, evaluator=evaluator, budget_fallback=target)

    def inspect_output(self, route, body):
        from .output import inspect_output
        try:
            return inspect_output(self, route, body)
        except TimeoutError:
            raise PolicyError("rule_timeout", 503) from None

    def route(self, payload, client_id="") -> Route:
        self.runtime.request_started()
        started_at = self.runtime.clock()
        definition = None
        try:
            if self.tokens and client_id not in self.tokens:
                raise PolicyError("unauthorized_client", 401)
            self.runtime.authorize_client(client_id)
            try:
                definition = self.loader.load()
            except (OSError, ValueError, TypeError, KeyError):
                raise PolicyError("invalid_policy_configuration", 503) from None
            self.definition = definition
            self.runtime.budgets = definition.budgets if definition else []
            self.update_controls(definition)
            budgets = tuple(deepcopy(definition.budgets)) if definition else ()
            route = None
            try:
                route = self._route(payload, definition, client_id)
                ticket = self.runtime.reserve(route.payload, started_at=started_at, budgets=budgets, client_id=client_id)
            except PolicyError as error:
                if not self.can_budget_fallback(error, definition):
                    raise
                # When assessment admission failed, re-run all checks with the explicitly configured classifier.
                assessment_failed = route is None
                if assessment_failed:
                    if not definition.budget_fallback.get("classifier"):
                        raise
                    route = self._route(payload, definition, client_id, fallback_assessment=True)
                route = self.fallback_route(route, definition, allow_same_model=assessment_failed)
                # Preserve every limit: model-scoped limits may exclude the cheaper destination; hard caps do not.
                ticket = self.runtime.reserve(route.payload, started_at=started_at, budgets=budgets, client_id=client_id)
                self.runtime.violation("budget_fallback", error.rules, direction="input", action="fallback",
                                       client_id=client_id, model=route.payload["model"], backend=route.budget_fallback)
                logger.warning("Budget %s exhausted; chat backend=%s model=%s", ",".join(error.rules),
                               route.budget_fallback, route.payload["model"])
                logger.info("Generating response", extra={"progress":
                            f"Generating response [{route.budget_fallback}: {route.payload['model']}]"})
            if budgets:
                route.payload.setdefault("max_tokens", 1024)
            return replace(route, ticket=ticket, client_id=client_id, budgets=budgets,
                           original_messages=deepcopy(payload["messages"]) if self.settings.log_chats else [])
        except TimeoutError:
            self.runtime.violation("rule_timeout", [], direction="input", action="block", client_id=client_id)
            raise PolicyError("rule_timeout", 503) from None
        except PolicyError as error:
            if self.settings.log_chats and isinstance(payload, dict) and isinstance(payload.get("messages"), list):
                messages = [{"role": message.get("role", "unknown"), "content": message["content"]}
                            for message in payload["messages"] if isinstance(message, dict)
                            and isinstance(message.get("content"), str)]
                self.runtime.log_chat(messages, client_id=client_id, status=error.code, action="block")
            self.runtime.violation(error.code, error.rules, direction="input", client_id=client_id,
                                   action="block", risks=error.risks, backend="none",
                                   thresholds={r["id"]: r["threshold"] for r in definition.semantic_rules
                                               if r["id"] in error.rules} if definition else {})
            if error.code == "policy_blocked":
                self.record_incident("input_violation", error.rules, direction="input", action="block")
            raise

    def _route(self, payload, definition=None, client_id="", fallback_assessment=False) -> Route:
        detector = definition.detector if definition else self.detector
        classifier = (definition.classifier if definition else None) or self.settings.classifier
        if fallback_assessment:
            classifier = definition.budget_fallback["classifier"]
        evaluator = self.injected_evaluator
        if fallback_assessment:
            evaluator = self.evaluator_for(classifier)
        if self.settings.deterministic:
            evaluator = None
        elif evaluator is None:
            with self.evaluator_lock:
                if classifier not in self.evaluator_cache:
                    self.evaluator_cache[classifier] = (LayaEvaluator(self.settings) if classifier == "laya"
                                                        else LLMEvaluator(self.settings) if classifier == "llm"
                                                        else JevEvaluator(self.settings, self.jev_transport))
                evaluator = self.evaluator_cache[classifier]
            semantic = definition and (definition.semantic_rules or
                       (definition.output_definition and definition.output_definition.semantic_rules))
            if classifier == "jev" and semantic and not self.settings.jev_key:
                raise PolicyError("missing_evaluator_key", 503)
        allowed = {"model", "messages", "temperature", "top_p", "max_tokens", "stream", "reasoning_budget"}
        if not isinstance(payload, dict) or set(payload) - allowed or payload.get("stream", False) is not False:
            raise PolicyError("unsupported_payload", 400)
        for name, maximum in (("temperature", 2), ("top_p", 1)):
            if name in payload and (type(payload[name]) not in (int, float)
                                    or not math.isfinite(payload[name]) or not 0 <= payload[name] <= maximum):
                raise PolicyError("invalid_" + name, 400)
        if "reasoning_budget" in payload and (type(payload["reasoning_budget"]) is not int or not -1 <= payload["reasoning_budget"] <= 32768):
            raise PolicyError("invalid_reasoning_budget", 400)
        if "model" in payload and (not isinstance(payload["model"], str) or not payload["model"]):
            raise PolicyError("invalid_model", 400)
        if "max_tokens" in payload and (type(payload["max_tokens"]) is not int or payload["max_tokens"] <= 0):
            raise PolicyError("invalid_max_tokens", 400)
        if self.settings.preserve_model:
            allowed_models = definition.allowed_models if definition and definition.allowed_models else [self.settings.main_model]
            if payload.get("model", self.settings.main_model) not in allowed_models:
                raise PolicyError("model_not_allowed", 403)
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or len(messages) > 1024 or any(
            not isinstance(m, dict) or set(m) != {"role", "content"}
            or m["role"] not in ("system", "user", "assistant", "developer")
            or not isinstance(m["content"], str) for m in messages
        ):
            raise PolicyError("unsupported_messages", 400)
        if sum(len(m["content"].encode("utf-8")) for m in messages) > 1048576:
            raise PolicyError("payload_too_large", 413)
        semantic_rules = definition.semantic_rules if definition and evaluator else []
        total = len(detector.rules) * len(messages) + len(semantic_rules) * sum(bool(m["content"]) for m in messages)
        completed = 0

        def progress(rule_id, description=None, backend="deterministic"):
            nonlocal completed
            completed += 1
            description = description or "Check " + rule_id.replace("_", " ")
            logger.info("Running rule (%s/%s): %s", completed, total, description,
                        extra={"progress": f"Running rule ({completed}/{total}): {description} [{backend}]"})

        matches = [(definition.scan(m["content"], progress) if definition else detector.scan(m["content"], progress))
                   for m in messages]
        risks = {}
        # A deterministic block needs no remote assessment.
        blocked = definition and any(definition.action_for(hit.rule_id) == "block"
                                          for hits in matches for hit in hits)
        if definition and definition.semantic_rules and not blocked and evaluator:
            for message in messages:
                if message["content"]:
                    for rule in semantic_rules:
                        progress(rule["id"], rule["description"], evaluator.name)
            try:
                semantic, risks = self.assess(evaluator, messages, definition.semantic_rules, budgets=definition.budgets, client_id=client_id)
            except EvaluationError:
                logger.error("backend=%s model=%s evaluation failed; chat backend not called", evaluator.name, evaluator.model)
                raise PolicyError(f"{evaluator.name}_unavailable", 503) from None
            for index, message in enumerate(messages):
                for rule in definition.semantic_rules:
                    name = f"m{index}_{rule['id']}"
                    if name not in risks:
                        continue
                    triggered = risks[name] >= rule["threshold"]
                    level = logging.ERROR if triggered and definition.action_for(rule["id"]) == "block" else logging.WARNING if triggered else logging.INFO
                    logger.log(level, "rule=%s message=%s backend=%s model=%s risk=%.1f%% threshold=%.1f%% result=%s action=%s",
                               rule["id"], index, evaluator.name, evaluator.model, risks[name], rule["threshold"],
                               "threshold reached" if triggered else "passed", definition.action_for(rule["id"]),
                               extra={"rule_check": True})
            matches = [hits + extra for hits, extra in zip(matches, semantic)]
        elif definition and definition.semantic_rules:
            logger.info("Semantic rules skipped: %s", "deterministic mode" if self.settings.deterministic else "deterministic block")
        rules = sorted({hit.rule_id for hits in matches for hit in hits})
        action = self.settings.policy if rules else "allow"
        if definition:
            actions = {definition.action_for(rule) for rule in rules}
            action = next((candidate for candidate in ("block", "local", "filter", "allow") if candidate in actions), "allow")
        threshold_details = []
        for rule in semantic_rules:
            rule_risks = [risk for name, risk in risks.items() if name.endswith("_" + rule["id"])]
            if rule_risks and max(rule_risks) >= rule["threshold"]:
                threshold_details.append(f"{rule['id']}: risk={max(rule_risks):.1f}% threshold={rule['threshold']:.1f}%")
        summary = f"action={action} rules={','.join(rules) or 'none'}"
        if threshold_details:
            summary += " | " + "; ".join(threshold_details)
        if action == "block":
            logger.error("%s | chat backend=none", summary)
            raise PolicyError("policy_blocked", 403, rules, risks)
        outbound = deepcopy(payload)
        replacements = {}
        filter_matches = [[hit for hit in hits if not definition or definition.action_for(hit.rule_id) == "filter"]
                          for hits in matches]
        if action == "filter" or (action == "local" and any(filter_matches)):
            tokens = {}
            # Reserve a namespace absent from all input, including literal tokens.
            prefix = "ANON"
            while any(f"[{prefix}:" in m["content"] for m in messages):
                prefix += "_"
            for message, hits in zip(outbound["messages"], filter_matches):
                text = message["content"]
                for start, end, rule_id in reversed(detector.spans(hits)):
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
        if definition and definition.allowed_models and outbound["model"] not in definition.allowed_models:
            raise PolicyError("model_not_allowed", 403)
        backend = "local" if action == "local" else "fallback" if fallback else "main"
        if rules:
            self.runtime.violation("policy_triggered", rules, direction="input", action=action, risks=risks,
                                   model=outbound["model"], backend=backend, client_id=client_id,
                                   thresholds={r["id"]: r["threshold"] for r in semantic_rules if r["id"] in rules})
        logger.log(logging.WARNING if action in ("filter", "local", "redirect") else logging.INFO,
                   "%s | chat backend=%s model=%s", summary, backend, outbound["model"])
        logger.info("Generating response", extra={"progress": f"Generating response [{backend}: {outbound['model']}]"})
        return Route(outbound, base, key, action, rules, replacements, risks=risks,
                     definition=definition, detector=detector, evaluator=evaluator)
