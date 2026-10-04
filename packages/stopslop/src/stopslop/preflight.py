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
    classifiers = {settings.classifier}
    if definition.budget_fallback and settings.fallback_classifier:
        classifiers.add(settings.fallback_classifier)
    for classifier in classifiers:
        if classifier == "laya" and "laya" not in sys.modules and importlib.util.find_spec("laya") is None:
            raise ValueError("Laya is not installed. Run uv sync --all-packages --extra laya, "
                             "select --classifier llm/jev, or explicitly use --deterministic")
        if classifier == "jev" and not settings.jev_key:
            raise ValueError("Semantic policies require STOPSLOP_JEV_KEY")
        if classifier == "ollama" and not settings.local_model:
            raise ValueError("Ollama assessment requires STOPSLOP_LOCAL_MODEL")
        if classifier == "llm" and not settings.key:
            raise ValueError("LLM assessment requires STOPSLOP_KEY")
