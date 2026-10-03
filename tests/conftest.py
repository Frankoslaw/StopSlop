"""Isolate persistent state, without contacting providers or touching real .env files."""
import os
import logging
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(str(root / "packages" / name / "src")
                                                   for name in ("stopslop", "stopslop_demo", "stopslop_top", "stopslop_proxy")))
    yield
    logger = logging.getLogger("stopslop.audit")
    for handler in list(logger.handlers):
        if getattr(handler, "stopslop_demo", False):
            logger.removeHandler(handler)
            handler.close()
