import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("clean_state", Path(__file__).resolve().parents[1] / "tools/clean_state.py")
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def test_clean_removes_state_and_preserves_configuration(tmp_path, monkeypatch):
    monkeypatch.setenv("STOPSLOP_STATE_FILE", "custom.db")
    names = ["custom.db", "custom.db-wal", "stopslop.sqlite3", "stopslop.sqlite3-journal", "metrics.json", "incidents.jsonl", "policy.dyn.toml", "metrics.json.sessions/session.json"]
    preserved = ["policy.toml", ".env", "source.py", ".venv/cache.sqlite3", ".git/state.sqlite3"]
    for name in names + preserved:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    cleanup.clean(tmp_path)
    assert all(not (tmp_path / name).exists() for name in names)
    assert all((tmp_path / name).exists() for name in preserved)
    assert not (tmp_path / "metrics.json.sessions").exists()


def test_clean_refuses_external_or_config_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("STOPSLOP_STATE_FILE", str(tmp_path.parent / "external.sqlite3"))
    with pytest.raises(ValueError, match="inside the workspace"):
        cleanup.clean(tmp_path)
    monkeypatch.setenv("STOPSLOP_STATE_FILE", "policy.toml")
    with pytest.raises(ValueError, match="extension"):
        cleanup.clean(tmp_path)
