import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tomllib

import httpx
import pytest

from stopslop.config import Settings
from stopslop.evaluators import assessment
from stopslop.policy import Policy, PolicyError
from stopslop.policy_file import PolicyFile
from stopslop.policy_format import policy_toml, read_policy
from stopslop.proxy import create_app
from stopslop.router import PolicyRouter
from stopslop.policy_schema import BUILTIN_TYPES


def payload(text="Hello"):
    return dict(model="main", messages=[dict(role="user", content=text)], max_tokens=10)


def response(text="Safe"):
    return dict(choices=[dict(message=dict(content=text))], usage=dict(prompt_tokens=2, completion_tokens=10))


def budget(name="main_daily", models=None, action="fallback", limit=10):
    return dict(name=name, type="fixed_quota", tokens="output", limit=limit, window_seconds=86400,
                reset_at="2026-01-01T00:00:00Z", models=models or ["main"], on_exhaustion=action)


def settings(tmp_path, route="fallback", **fields):
    path = tmp_path / "policy.toml"
    data = dict(version=1, default_action="filter", rules=[dict(type="email", action="filter")],
                budgets=[budget()], allowed_models=["main", "cheap", "free"], budget_fallback=dict(route=route)) | fields
    path.write_text(policy_toml(data), encoding="utf-8")
    return Settings(main_key="main-key", main_model="main", deterministic=True, policy_file=str(path),
                    state_file=str(tmp_path / "state.sqlite3"), fallback_base_url="https://cheap.example/v1",
                    fallback_model="cheap", fallback_key="cheap-key", local_model="free")


def exhaust(policy):
    ticket = policy.runtime.reserve(payload())
    policy.runtime.finish(ticket, response())


def test_typed_builtin_regex_and_semantic_rules():
    definition = PolicyFile(data=dict(version=1, rules=[
        dict(type="builtin.email", action="filter"),
        dict(type="regex", name="project", pattern=r"\bProject Raven\b", action="block"),
        dict(type="regex", pattern="another project", action="block"),
        dict(type="semantic", name="confidential", description="Block confidential information.", action="block")]))
    assert definition.action_for("email") == "filter"
    assert "project" in {hit.rule_id for hit in definition.scan("Project Raven")}
    assert any(hit.rule_id.startswith("regex_") for hit in definition.scan("another project"))
    assert definition.semantic_rules[0]["id"] == "confidential"


@pytest.mark.parametrize("rule_type", sorted(BUILTIN_TYPES))
def test_builtin_namespace_roundtrip(rule_type, tmp_path):
    data = dict(version=1, rules=[dict(type="builtin." + rule_type, action="filter")],
                output=dict(rules=[dict(type="builtin." + rule_type, action="block")]))
    definition = PolicyFile(data=data)
    assert definition.action_for(rule_type) == "filter"
    text = policy_toml(data)
    assert text.count('type = "builtin.' + rule_type + '"') == 2
    path = tmp_path / "policy.toml"
    path.write_text(text, encoding="utf-8")
    assert read_policy(path)["rules"][0]["id"] == rule_type


@pytest.mark.parametrize("rule", [
    dict(type="regex", name="custom", action="block"),
    dict(type="semantic", name="custom", pattern="bad", action="block"),
    dict(type="regex", name="custom", description="bad", action="block"),
    dict(type="email", id="email", action="block"),
    dict(type="regex", name="email", pattern="override", action="block"),
    dict(type="builtin.unknown", action="block"),
    dict(type="builtin.regex", pattern="override", action="block"),
])
def test_invalid_rule_types_rejected(rule):
    with pytest.raises(ValueError):
        PolicyFile(data=dict(version=1, rules=[rule]))


def test_typed_output_override_and_toml_export(tmp_path):
    data = dict(version=1, default_action="allow", rules=[dict(type="regex", name="restricted", pattern="restricted", action="allow")],
                output=dict(rules=[dict(type="regex", name="restricted", action="block")]))
    text = policy_toml(data)
    assert "id =" not in text
    path = tmp_path / "policy.toml"
    path.write_text(text, encoding="utf-8")
    policy = Policy(Settings(main_key="key", deterministic=True, policy_file=str(path)))
    route = policy.route(payload())
    with pytest.raises(PolicyError, match="output_blocked"):
        policy.inspect_output(route, response("restricted"))
    assert read_policy(path)["rules"][0]["id"] == "restricted"


def test_shipped_policy_uses_types_and_names():
    raw = tomllib.loads((Path(__file__).resolve().parents[1] / "policy.toml").read_text(encoding="utf-8"))
    assert all("type" in r and "id" not in r for r in raw["rules"] + raw["output"]["rules"])
    assert any(r["type"] == "regex" for r in raw["rules"])
    assert all(r["type"].startswith("builtin.") for r in raw["rules"] if "name" not in r)
    assert all("name" in b and "id" not in b for b in raw["budgets"])
    assert all("type" in r and "id" not in r for r in raw["permissions"]["rules"])


@pytest.mark.parametrize("transport", ["router", "proxy"])
@pytest.mark.parametrize("target", ["fallback", "local"])
def test_budget_fallback_routes_filtered_request_and_checks_output(tmp_path, transport, target):
    config = settings(tmp_path, target)
    calls = []

    def backend(request):
        body = json.loads(request.content)
        calls.append((str(request.url), body, request.headers["authorization"]))
        assert "jane@example.org" not in request.content.decode()
        return httpx.Response(200, json=response(body["messages"][0]["content"]))

    if transport == "router":
        router = PolicyRouter(config, httpx.MockTransport(backend))
        exhaust(router.policy)
        with httpx.Client(transport=router) as client:
            result = client.post("http://test/v1/chat/completions", json=payload("jane@example.org"))
    else:
        app = create_app(config, httpx.MockTransport(backend))
        exhaust(app.state.policy)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                return await client.post("/v1/chat/completions", json=payload("jane@example.org"))
        result = asyncio.run(run())
    assert result.status_code == 200
    assert result.json()["choices"][0]["message"]["content"] == "jane@example.org"
    assert result.headers["X-StopSlop-Action"] == "filter"
    assert result.headers["X-StopSlop-Budget-Fallback"] == target
    assert len(calls) == 1
    assert calls[0][1]["model"] == ("cheap" if target == "fallback" else "free")
    assert calls[0][2] == ("Bearer cheap-key" if target == "fallback" else "Bearer local")


def test_hard_global_limit_and_fallback_model_quota_are_preserved(tmp_path):
    for limits in ([budget(), budget("cheap_daily", ["cheap"], action="block", limit=1)],
                   [budget(), {k: v for k, v in budget("overall", action="block").items() if k != "models"}]):
        policy = Policy(settings(tmp_path, budgets=limits))
        exhaust(policy)
        with pytest.raises(PolicyError, match="budget_exceeded"):
            policy.route(payload())
        policy.runtime.close()
        # Keep iterations independent without dropping either model's quota in a live runtime.
        tmp_path = tmp_path / "next"
        tmp_path.mkdir()


@pytest.mark.parametrize("change,code", [
    (dict(fallback_model="unapproved"), "model_not_allowed"),
    (dict(fallback_key=""), "missing_budget_fallback"),
    (dict(fallback_model="main"), "budget_fallback_same_model"),
])
def test_invalid_fallback_destinations_fail_closed(tmp_path, change, code):
    policy = Policy(replace(settings(tmp_path), **change))
    exhaust(policy)
    with pytest.raises(PolicyError, match=code):
        policy.route(payload())


def test_local_privacy_rule_cannot_fallback_to_cloud(tmp_path):
    config = settings(tmp_path, budgets=[budget(models=["free"])], rules=[dict(type="profanity", action="local")])
    policy = Policy(config)
    policy.runtime.finish(policy.runtime.reserve(payload() | {"model": "free"}), response())
    with pytest.raises(PolicyError, match="budget_fallback_not_allowed"):
        policy.route(payload("shit"))


def test_blocked_content_never_uses_budget_fallback(tmp_path):
    policy = Policy(settings(tmp_path, rules=[dict(type="secret", action="block")]))
    exhaust(policy)
    with pytest.raises(PolicyError, match="policy_blocked"):
        policy.route(payload("api_key=private"))


def test_default_budget_behavior_still_blocks(tmp_path):
    policy = Policy(settings(tmp_path, budgets=[budget(action="block")]))
    exhaust(policy)
    with pytest.raises(PolicyError, match="budget_exceeded"):
        policy.route(payload())


def test_concurrent_fallback_reservations_respect_cheap_model_quota(tmp_path):
    policy = Policy(settings(tmp_path, budgets=[budget(), budget("cheap_daily", ["cheap"], action="block")]))
    exhaust(policy)
    first = policy.route(payload())
    assert first.payload["model"] == "cheap"
    with pytest.raises(PolicyError, match="budget_exceeded"):
        policy.route(payload())
    assert first.ticket in policy.runtime.pending


@pytest.mark.parametrize("transport", ["router", "proxy"])
def test_fallback_generated_secrets_are_withheld(tmp_path, transport):
    config = settings(tmp_path, rules=[dict(type="secret", action="block")])
    backend = httpx.MockTransport(lambda _: httpx.Response(200, json=response("api_key=generated-secret")))
    if transport == "router":
        router = PolicyRouter(config, backend)
        exhaust(router.policy)
        with httpx.Client(transport=router) as client:
            result = client.post("http://test/v1/chat/completions", json=payload())
    else:
        app = create_app(config, backend)
        exhaust(app.state.policy)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                return await client.post("/v1/chat/completions", json=payload())
        result = asyncio.run(run())
    assert result.status_code == 403 and result.json()["error"]["code"] == "output_blocked"
    assert "generated-secret" not in result.text


def test_semantic_budget_fallback_keeps_all_checks(tmp_path):
    rules = [dict(type="semantic", name="confidential", description="Block confidential content.", action="block")]
    config = replace(settings(tmp_path, rules=rules, classifier="llm", budget_fallback=dict(route="local", classifier="laya")),
                     deterministic=False)
    calls = []

    class LocalEvaluator:
        name, model = "laya", "classifier"

        def assess(self, messages, semantic_rules):
            calls.append(messages)
            return assessment(messages, semantic_rules, {f"m{i}_{r['id']}": 0.95 if "confidential" in m["content"] else 0
                                                        for i, m in enumerate(messages) for r in semantic_rules})

    policy = Policy(config)
    policy.evaluator_cache["laya"] = LocalEvaluator()
    exhaust(policy)
    safe = policy.route(payload())
    assert safe.payload["model"] == "free" and safe.budget_fallback == "local"
    policy.finish(safe, response())
    with pytest.raises(PolicyError, match="policy_blocked"):
        policy.route(payload("confidential information"))
    assert len(calls) == 2


def test_output_semantics_use_local_classifier_when_paid_quota_exhausts(tmp_path):
    rules = [dict(type="semantic", name="confidential", description="Block confidential content.", action="block")]
    config = replace(settings(tmp_path, rules=rules, classifier="llm", budget_fallback=dict(route="local", classifier="laya")),
                     deterministic=False)

    class LocalEvaluator:
        name, model = "laya", "classifier"

        def assess(self, messages, semantic_rules):
            return assessment(messages, semantic_rules, {f"m{i}_{r['id']}": 0.95 if "confidential" in m["content"] else 0
                                                        for i, m in enumerate(messages) for r in semantic_rules})

    policy = Policy(config)
    policy.evaluator_cache["laya"] = LocalEvaluator()
    # Build an admitted route without a provider call; output must retain semantic checks.
    policy.settings = replace(config, deterministic=True)
    route = policy.route(payload())
    policy.settings = config
    route = replace(route, evaluator=policy.evaluator)
    with pytest.raises(PolicyError, match="output_blocked"):
        policy.inspect_output(route, response("confidential information"))
