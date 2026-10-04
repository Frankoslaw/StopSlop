"""Bounded, best-effort incident generation outside the request path."""
import logging
from pathlib import Path
from threading import Lock, Thread
from time import monotonic

from .adapt import generate_policy

_lock = Lock()
_jobs = {}
_cooldown = 60


def schedule_generation(settings):
    """Coalesce bursts per state file; persisted incidents survive failed jobs."""
    key = str(Path(settings.state_file).resolve())
    with _lock:
        busy, last = _jobs.get(key, (False, float("-inf")))
        if busy or monotonic() - last < _cooldown:
            return False
        _jobs[key] = (True, monotonic())

    def run():
        try:
            generate_policy(settings, settings.policy_file, settings.autogen_output_file)
        except Exception:
            logging.getLogger("stopslop.audit").warning("Deferred policy generation failed; existing controls retained")
        finally:
            with _lock:
                _jobs[key] = (False, monotonic())

    try:
        Thread(target=run, name="stopslop-autogen", daemon=True).start()
    except RuntimeError:
        with _lock:
            _jobs[key] = (False, monotonic())
        return False
    return True
