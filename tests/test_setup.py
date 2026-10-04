import importlib.util
from pathlib import Path

import pytest
from dotenv import dotenv_values

spec = importlib.util.spec_from_file_location("local_setup", Path(__file__).resolve().parents[1] / "tools/setup.py")
local_setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_setup)


def test_environment_uses_model_reported_by_ollama(tmp_path):
    (tmp_path / ".env.example").write_text("# template\nSTOPSLOP_PROVIDER=nvidia\nSTOPSLOP_KEY=\n")
    (tmp_path / ".env").write_text("existing config")
    local_setup.write_environment(tmp_path, [{"name": "other:latest"}, {"name": "qwen3:0.6b"}])
    values = dotenv_values(tmp_path / ".env")
    assert values["STOPSLOP_PROVIDER"] == "ollama"
    assert values["STOPSLOP_MODEL"] == values["STOPSLOP_LOCAL_MODEL"] == "qwen3:0.6b"
    assert values["STOPSLOP_CLASSIFIER"] == "laya"
    assert values["STOPSLOP_BASE_URL"] == "http://127.0.0.1:11434/v1"
    assert values["STOPSLOP_JEV_KEY"] == ""
    assert not (tmp_path / ".env.setup.tmp").exists()


def test_missing_model_preserves_environment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("existing config")
    with pytest.raises(ValueError, match="did not report"):
        local_setup.write_environment(tmp_path, [{"name": "other:latest"}])
    assert env.read_text() == "existing config"


def test_setup_installs_laya_then_pulls_and_reads_ollama(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text("STOPSLOP_PROVIDER=ollama\n")
    calls = []
    monkeypatch.setattr(local_setup, "ollama_executable", lambda: "ollama")
    monkeypatch.setattr(local_setup, "ensure_server", lambda *args: None)
    monkeypatch.setattr(local_setup.subprocess, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(local_setup, "installed_models", lambda: [{"name": "qwen3:0.6b"}])
    local_setup.setup(tmp_path)
    assert calls[0][-4:] == ["sync", "--all-packages", "--extra", "laya"]
    assert calls[1] == ["ollama", "pull", "qwen3:0.6b"]
    assert dotenv_values(tmp_path / ".env")["STOPSLOP_CLASSIFIER"] == "laya"


def test_local_qwen_keeps_reply_tokens_for_answer(tmp_path):
    from stopslop.config import Settings
    from stopslop.policy import Policy

    policy = Policy(Settings(provider="ollama", model="qwen3:0.6b", deterministic=True,
                             state_file=str(tmp_path / "state.db")))
    try:
        route = policy.route({"messages": [{"role": "user", "content": "Explain how rain forms."}],
                              "max_tokens": 96})
        assert route.payload["reasoning_effort"] == "none"
        assert route.payload["max_tokens"] == 96
    finally:
        policy.runtime.close()


def test_local_onboarding_final_block_is_deterministic():
    from stopslop.policy_file import PolicyFile
    from stopslop_demo.scenarios import SCENARIOS

    definition = PolicyFile(str(Path(__file__).resolve().parents[1] / "policy.toml"))
    final = SCENARIOS["local"][-1]
    assert final.expected_action == "block"
    assert any(definition.action_for(hit.rule_id) == "block" for hit in definition.scan(final.prompt))
