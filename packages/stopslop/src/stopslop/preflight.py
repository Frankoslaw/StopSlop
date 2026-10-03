"""Check optional semantic dependencies before starting an interactive service."""
import importlib.util
import sys
from pathlib import Path
from .repository import SQLiteRepository

from .policy_loader import PolicyLoader


def check_semantic_setup(settings):
    if settings.deterministic:
        return
    repository = SQLiteRepository(settings.state_file, read_only=True) if Path(settings.state_file).is_file() else None
    try:
        definition = PolicyLoader(settings, repository).load()
    finally:
        if repository:
            repository.close()
    if not definition or not (definition.semantic_rules or
                             (definition.output_definition and definition.output_definition.semantic_rules)):
        return
    classifier = definition.classifier or settings.classifier
    if classifier == "laya" and "laya" not in sys.modules and importlib.util.find_spec("laya") is None:
        raise ValueError("Laya is not installed. Run uv sync --all-packages --extra laya, "
                         "select classifier llm/jev in the policy file, or explicitly use --deterministic")
    if classifier == "jev" and not settings.jev_key:
        raise ValueError("Semantic policies require STOPSLOP_JEV_KEY")
    if classifier == "llm" and not settings.main_key:
        raise ValueError("LLM assessment requires STOPSLOP_MAIN_KEY")
