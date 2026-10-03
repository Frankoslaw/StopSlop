import json
import sys
from types import SimpleNamespace

import pytest

from stopslop.config import Settings
from stopslop.laya import LayaEvaluator
from stopslop.policy import Policy, PolicyError
from stopslop.policy_file import PolicyFile


def configuration(tmp_path, classifier=None):
    data = {"version": 1, "rules": [{"id": "nda", "description": "Do not disclose NDA information.", "threshold": 75, "action": "block"}]}
    if classifier is not None:
        data["classifier"] = classifier
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(data))
    return Settings(main_key="mock", policy_file=str(path))


def test_local_default_lazy_load_and_reuse(tmp_path, monkeypatch):
    calls = []
    def predict(state, questions):
        assert state["messages"][0]["content"] == "Safe agenda"
        assert questions["m0_nda"]["instructions"]["target_index"] == 0
        return {"answers": {name: {"type": "noul", "noul": .1} for name in questions}}
    def load(model, device):
        calls.append((model, device))
        return SimpleNamespace(predict=predict)
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=load))
    policy = Policy(configuration(tmp_path))
    assert isinstance(policy.evaluator, LayaEvaluator) and not calls
    for _ in range(2):
        route = policy.route({"messages": [{"role": "user", "content": "Safe agenda"}]})
        assert route.action == "allow" and route.risks == {"m0_nda": 10}
    assert calls == [("convaiinnovations/laya", "cpu")]


@pytest.mark.parametrize("answer", [{}, {"type": "choice", "noul": .9}, {"type": "noul", "noul": True}, {"type": "noul", "noul": 1.1}, {"type": "noul", "noul": float("nan")}])
def test_local_invalid_output_fails_closed(tmp_path, monkeypatch, answer):
    agent = SimpleNamespace(predict=lambda *args: {"answers": {"m0_nda": answer}})
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda *args, **kwargs: agent))
    with pytest.raises(PolicyError, match="laya_unavailable"):
        Policy(configuration(tmp_path)).route({"messages": [{"role": "user", "content": "NDA details"}]})


def test_policy_classifier_selects_jev(tmp_path):
    from dataclasses import replace
    from stopslop.jev import JevEvaluator
    config = configuration(tmp_path, "jev")
    with pytest.raises(ValueError, match="STOPSLOP_JEV_KEY"):
        Policy(config)
    assert isinstance(Policy(replace(config, jev_key="test")).evaluator, JevEvaluator)
    assert isinstance(Policy(replace(configuration(tmp_path, "laya"), classifier="jev")).evaluator, LayaEvaluator)


@pytest.mark.parametrize("classifier", ["unknown", 1, {}, [], False])
def test_invalid_classifier(tmp_path, classifier):
    with pytest.raises(ValueError, match="classifier"):
        PolicyFile(configuration(tmp_path, classifier).policy_file)


def test_local_load_failure_is_closed(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive input")
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=fail))
    with pytest.raises(PolicyError, match="^laya_unavailable$"):
        Policy(configuration(tmp_path)).route({"messages": [{"role": "user", "content": "NDA details"}]})


def test_prepare_loads_once_before_assessment(tmp_path, monkeypatch):
    calls = []
    def load(model, device):
        calls.append((model, device))
        return SimpleNamespace(predict=lambda state, questions: {"answers": {
            name: {"type": "noul", "noul": .1} for name in questions}})
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=load))
    policy = Policy(configuration(tmp_path))
    policy.prepare_models()
    policy.prepare_models()
    policy.route({"messages": [{"role": "user", "content": "Safe agenda"}]})
    assert calls == [("convaiinnovations/laya", "cpu")]


def test_prepare_failure_stops_before_requests(tmp_path, monkeypatch):
    from stopslop.evaluators import EvaluationError
    def fail(*args, **kwargs):
        raise RuntimeError("download failed with private details")
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=fail))
    policy = Policy(configuration(tmp_path))
    with pytest.raises(EvaluationError, match="^laya_unavailable$"):
        policy.prepare_models()
    assert policy.runtime.stats["requests"] == 0


def test_prepare_includes_laya_budget_fallback(tmp_path, monkeypatch):
    from dataclasses import replace
    from pathlib import Path
    config = configuration(tmp_path, "llm")
    path = Path(config.policy_file)
    data = json.loads(path.read_text())
    data["budget_fallback"] = {"route": "local", "classifier": "laya"}
    path.write_text(json.dumps(data))
    calls = []
    monkeypatch.setitem(sys.modules, "laya", SimpleNamespace(load=lambda model, device: calls.append(model) or object()))
    policy = Policy(config)
    policy.prepare_models()
    assert calls == [config.laya_model]
    deterministic = Policy(replace(config, deterministic=True))
    deterministic.prepare_models()
    assert calls == [config.laya_model]
