from dataclasses import dataclass
import os
import json
import math
from urllib.parse import urlsplit
import ipaddress
from pathlib import Path
from dotenv import load_dotenv

POLICIES = ("block", "redirect", "filter", "passthrough")

@dataclass(frozen=True)
class Settings:
    main_provider: str = "nvidia"
    main_base_url: str | None = None
    main_model: str | None = None
    main_key: str = ""
    policy: str = "block"
    fallback_base_url: str = ""
    fallback_model: str = ""
    fallback_key: str = ""
    rules_file: str = ""
    policy_file: str = ""
    local_base_url: str = "http://127.0.0.1:11434/v1"
    local_model: str = ""
    local_key: str = "local"
    deterministic: bool = False
    classifier: str = "laya"
    classifier_override: str = ""
    laya_model: str = "convaiinnovations/laya"
    laya_device: str = "cpu"
    jev_key: str = ""
    jev_base_url: str = "https://api.typesafe.ai/v1"
    jev_model: str = "jev-latest"
    preserve_model: bool = False
    state_file: str = "stopslop.sqlite3"
    log_chats: bool = True
    max_request_bytes: int = 2097152
    max_response_bytes: int = 4194304
    access_tokens: str = ""
    client_token: str = ""
    dynamic_policy_file: str = ""
    timeout: float = 120.0

    def __post_init__(self):
        providers = {
            "nvidia": ("https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"),
            "openai": ("https://api.openai.com/v1", "gpt-4.1-mini"),
            "ollama": ("http://127.0.0.1:11434/v1", None),
        }
        if self.main_provider not in providers:
            raise ValueError("main_provider must be nvidia, openai or ollama")
        base_url, model = providers[self.main_provider]
        object.__setattr__(self, "main_base_url", self.main_base_url or base_url)
        object.__setattr__(self, "main_model", self.main_model or model)
        if not self.main_model:
            raise ValueError("ollama requires main_model matching an installed model")
        if self.main_provider == "ollama" and not self.main_key:
            object.__setattr__(self, "main_key", "ollama")
        for name in ("max_request_bytes", "max_response_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.classifier_override and self.classifier_override not in ("laya", "jev", "llm"):
            raise ValueError("classifier_override must be laya, jev or llm")
        if self.classifier not in ("laya", "jev", "llm"):
            raise ValueError("classifier must be laya, jev or llm")
        if self.policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        if self.policy == "redirect" and not all((self.fallback_base_url, self.fallback_model, self.fallback_key)):
            raise ValueError("redirect requires fallback URL, model and key")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.access_tokens:
            tokens = json.loads(self.access_tokens)
            if (not isinstance(tokens, dict) or not tokens
                    or any(not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in tokens.items())
                    or len(set(tokens.values())) != len(tokens)):
                raise ValueError("access_tokens must map unique client IDs to unique bearer tokens")
        url = urlsplit(self.local_base_url)
        try:
            loopback = ipaddress.ip_address(url.hostname or "").is_loopback
        except ValueError:
            loopback = url.hostname == "localhost"
        if url.scheme not in ("http", "https") or not loopback or url.username or url.password or url.query or url.fragment:
            raise ValueError("local_base_url must be a loopback HTTP endpoint")

    @classmethod
    def load(cls, env_file: str = ".env", **overrides):
        load_dotenv(Path(env_file), override=False)
        values = {}
        for name in cls.__dataclass_fields__:
            value = os.getenv("STOPSLOP_" + name.upper())
            if value is not None:
                if name in ("deterministic", "preserve_model", "log_chats"):
                    if value.lower() not in ("true", "false", "1", "0"):
                        raise ValueError(f"STOPSLOP_{name.upper()} must be true or false")
                    values[name] = value.lower() in ("true", "1")
                else:
                    values[name] = (float(value) if name == "timeout" else int(value)
                                    if name in ("max_request_bytes", "max_response_bytes") else value)
        provider = overrides.get("main_provider") or values.get("main_provider", "nvidia")
        for field in ("base_url", "model", "key"):
            value = os.getenv(f"STOPSLOP_{provider.upper()}_{field.upper()}")
            if value:
                values["main_" + field] = value
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)
