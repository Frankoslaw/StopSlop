import argparse
import json
from threading import Event

import httpx

from stopslop import autogen
from stopslop.adapt import generate_policy, record_incident
from stopslop.config import Settings, add_settings_arguments
from stopslop.policy import Policy


def test_flags_and_dotenv(tmp_path):
    parser = argparse.ArgumentParser()
    add_settings_arguments(parser)
    assert parser.parse_args(["--autogen"]).autogen is True
    assert parser.parse_args(["--no-autogen"]).autogen is False
    env = tmp_path / ".env"
    env.write_text("STOPSLOP_AUTOGEN=false\n")
    assert Settings.load(env).autogen is False


def test_worker_returns_without_waiting_and_coalesces(tmp_path, monkeypatch):
    entered, release, finished = Event(), Event(), Event()
    calls = []

    def generate(*args):
        calls.append(args)
        entered.set()
        assert release.wait(5)
        finished.set()

    monkeypatch.setattr(autogen, "generate_policy", generate)
    settings = Settings(state_file=str(tmp_path / "state.db"))
    try:
        assert autogen.schedule_generation(settings)
        assert entered.wait(2)
        assert not finished.is_set()
        assert not autogen.schedule_generation(settings)
    finally:
        release.set()
    assert finished.wait(2)
    assert len(calls) == 1


def test_disabled_feature_never_schedules(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(autogen, "schedule_generation", calls.append)
    policy = Policy(Settings(deterministic=True, state_file=str(tmp_path / "state.db")))
    policy.record_incident("input_violation", ["secret"])
    assert calls == []
    policy.runtime.close()


def test_generation_uses_local_model_and_exports(tmp_path):
    base = tmp_path / "policy.toml"
    base.write_text('version = 1\nallowed_models = ["qwen3:0.6b"]\n')
    settings = Settings(key="hosted", local_model="qwen3:0.6b", state_file=str(tmp_path / "state.db"))
    record_incident(settings.state_file, "output_violation", ["secret"])

    def backend(request):
        payload = json.loads(request.content)
        assert request.url.host == "127.0.0.1"
        assert payload["model"] == "qwen3:0.6b"
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["reasoning_effort"] == "none"
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(
            {"version": 1, "rules": [{"id": "dyn_exploit", "literal": "exploit_signature"}]})}}]})

    output = tmp_path / "policy.dyn.toml"
    generate_policy(settings, base, output, httpx.MockTransport(backend))
    assert "dyn_exploit" in output.read_text()


def test_ollama_fallback_evaluator_is_independent(tmp_path):
    policy = Policy(Settings(classifier="llm", key="hosted", local_model="qwen3:0.6b",
                             fallback_classifier="ollama", state_file=str(tmp_path / "state.db")))
    evaluator = policy.evaluator_for("ollama")
    assert evaluator.model == "qwen3:0.6b"
    assert evaluator.settings.base_url == "http://127.0.0.1:11434/v1"
    assert evaluator.settings.key == "local"
    policy.runtime.close()
