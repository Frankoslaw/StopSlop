"""HTTP gateway for OpenAI, NVIDIA and Ollama text chat."""
from .app import create_app

__all__ = ["create_app"]
