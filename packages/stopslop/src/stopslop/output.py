"""Inspect model-generated text before restoring request-local anonymous tokens."""
from copy import deepcopy

from .evaluators import EvaluationError
from .rules import Detector


def inspect_output(policy, route, body):
    from .policy import PolicyError
    if not isinstance(body, dict) or not isinstance(body.get("choices"), list):
        raise PolicyError("unsupported_response", 502)
    result = deepcopy(body)
    fields = []
    for choice in result["choices"]:
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise PolicyError("unsupported_response", 502)
        message = choice["message"]
        if any(message.get(key) for key in ("tool_calls", "function_call", "audio")):
            raise PolicyError("unsupported_response", 502)
        for key, value in message.items():
            if key == "role" or value is None:
                continue
            if not isinstance(value, str):
                raise PolicyError("unsupported_response", 502)
            fields.append((message, key, value))
    definition = route.definition
    detector = route.detector or policy.detector
    inherit = definition.output_inherit if definition else True
    explicit = definition.output_definition if definition else None
    context = route.payload["messages"]
    generated = [{"role": "assistant", "content": text} for _, _, text in fields]
    decisions = [[] for _ in fields]
    risks = {}

    def action(rule_id):
        if definition and rule_id in definition.output_actions:
            return definition.output_actions[rule_id]
        input_action = definition.action_for(rule_id) if definition else policy.settings.policy
        if input_action in ("allow", "passthrough"):
            return "allow"
        if input_action == "filter":
            return "filter"
        return definition.output_default if definition else "block"

    if inherit:
        for index, (_, _, text) in enumerate(fields):
            hits = definition.scan(text) if definition else detector.scan(text)
            decisions[index].extend((hit, action(hit.rule_id)) for hit in hits)
    if explicit:
        for index, (_, _, text) in enumerate(fields):
            decisions[index].extend((hit, definition.output_actions[hit.rule_id])
                                    for hit in explicit.scan(text)
                                    if hit.rule_id in definition.output_actions)
    semantic = {rule["id"]: rule for rule in (definition.semantic_rules if definition and inherit else [])}
    if explicit:
        semantic.update({rule["id"]: rule for rule in explicit.semantic_rules})
    already_blocked = any(a in ("block", "block_device") for hits in decisions for _, a in hits)
    if semantic and route.evaluator and generated and not already_blocked:
        try:
            matches, all_risks = policy.assess(route.evaluator, context + generated, list(semantic.values()), len(context))
        except EvaluationError:
            raise PolicyError(f"{route.evaluator.name}_unavailable", 503) from None
        for index, hits in enumerate(matches[len(context):]):
            decisions[index].extend((hit, action(hit.rule_id)) for hit in hits)
        risks = {name: risk for name, risk in all_risks.items()
                 if int(name.split("_", 1)[0][1:]) >= len(context)}
    rules = sorted({hit.rule_id for hits in decisions for hit, a in hits if a != "allow"})
    actions = {a for hits in decisions for _, a in hits if a != "allow"}
    outcome = next((a for a in ("block_device", "block", "filter", "warn") if a in actions), "allow")
    if rules:
        policy.runtime.violation("output_violation", rules, direction="output", action=outcome,
                                 client_id=route.client_id, model=route.payload["model"], risks=risks,
                                 thresholds={rid: rule["threshold"] for rid, rule in semantic.items() if rid in rules})
        policy.record_incident("data_leak" if outcome == "filter" else "output_violation", rules,
                               direction="output", model=route.payload["model"], action=outcome)
    if outcome in ("block_device", "block"):
        if outcome == "block_device" and route.client_id:
            policy.runtime.suspend_client(route.client_id)
        raise PolicyError("output_blocked", 403, rules, risks)
    for (message, key, text), hits in zip(fields, decisions):
        message[key] = detector.redact(text, [hit for hit, a in hits if a == "filter"])
    if route.client_id:
        policy.runtime.authorize_client(route.client_id)
    return route.restore(result), outcome, rules, risks
