from dataclasses import dataclass, field
import os
import json
import math
from urllib.parse import urlsplit
import ipaddress
import argparse
from pathlib import Path
from dotenv import dotenv_values

POLICIES = ("block", "redirect", "filter", "passthrough")

@dataclass(frozen=True)
class Settings:
    provider: str = "nvidia"
    base_url: str | None = None
    model: str | None = None
    key: str = field(default="", repr=False)
    policy: str = "block"
    fallback_base_url: str = ""
    fallback_model: str = ""
    fallback_key: str = field(default="", repr=False)
    rules_file: str = ""
    policy_file: str = ""
    local_base_url: str = "http://127.0.0.1:11434/v1"
    local_model: str = ""
    local_key: str = field(default="local", repr=False)
    deterministic: bool = False
    classifier: str = "laya"
    fallback_classifier: str = ""
    laya_model: str = "convaiinnovations/laya"
    laya_device: str = "cpu"
    jev_key: str = field(default="", repr=False)
    jev_base_url: str = "https://api.typesafe.ai/v1"
    jev_model: str = "jev-latest"
    preserve_model: bool = False
    state_file: str = "stopslop.sqlite3"
    log_chats: bool = True
    max_request_bytes: int = 2097152
    max_response_bytes: int = 4194304
    access_tokens: str = field(default="", repr=False)
    client_token: str = field(default="", repr=False)
    dynamic_policy_file: str = ""
    autogen: bool = False
    autogen_output_file: str = "policy.dyn.toml"
    timeout: float = 120.0

    def __post_init__(self):
        providers = {
            "nvidia": ("https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"),
            "openai": ("https://api.openai.com/v1", "gpt-4.1-mini"),
            "ollama": ("http://127.0.0.1:11434/v1", None),
        }
        if self.provider not in providers:
            raise ValueError("provider must be nvidia, openai or ollama")
        base_url, model = providers[self.provider]
        object.__setattr__(self, "base_url", self.base_url or base_url)
        object.__setattr__(self, "model", self.model or model)
        if not self.model:
            raise ValueError("ollama requires model matching an installed model")
        if self.provider == "ollama" and not self.key:
            object.__setattr__(self, "key", "ollama")
        for name in ("max_request_bytes", "max_response_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.fallback_classifier and self.fallback_classifier not in ("laya", "jev", "llm", "ollama"):
            raise ValueError("fallback_classifier must be laya, jev, llm or ollama")
        if self.classifier not in ("laya", "jev", "llm", "ollama"):
            raise ValueError("classifier must be laya, jev, llm or ollama")
        if self.policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        if self.policy == "redirect" and not all((self.fallback_base_url, self.fallback_model, self.fallback_key)):
            raise ValueError("redirect requires fallback URL, model and key")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.autogen and (not self.policy_file or not self.local_model):
            raise ValueError("autogen requires policy_file and local_model")
        if self.autogen and self.dynamic_policy_file and Path(self.dynamic_policy_file).resolve() == Path(self.autogen_output_file).resolve():
            raise ValueError("autogen export is already loaded from SQLite; do not also load it as dynamic_policy_file")
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
        environment = {**dotenv_values(Path(env_file)), **os.environ}
        values = {}
        for name in cls.__dataclass_fields__:
            if overrides.get(name) is not None:
                continue
            value = environment.get("STOPSLOP_" + name.upper())
            if value is not None:
                if name in ("deterministic", "preserve_model", "log_chats", "autogen"):
                    if value.lower() not in ("true", "false", "1", "0"):
                        raise ValueError(f"STOPSLOP_{name.upper()} must be true or false")
                    values[name] = value.lower() in ("true", "1")
                else:
                    values[name] = (float(value) if name == "timeout" else int(value)
                                    if name in ("max_request_bytes", "max_response_bytes") else value)
        values.update({k: v for k, v in overrides.items() if v is not None})
        if not values.get("policy_file") and not values.get("rules_file") and "policy" not in values and Path("policy.toml").is_file():
            values["policy_file"] = "policy.toml"
        return cls(**values)


def add_settings_arguments(parser):
    """One flag per runtime setting; None lets environment/default values apply."""
    parser.add_argument("--env-file", default=".env")
    choices = {"provider": ("nvidia", "openai", "ollama"), "policy": POLICIES,
               "classifier": ("laya", "jev", "llm", "ollama"), "fallback_classifier": ("laya", "jev", "llm", "ollama")}
    groups = {
        "upstream": parser.add_argument_group("Upstream provider"),
        "enforcement": parser.add_argument_group("Policy and assessment"),
        "fallback": parser.add_argument_group("Optional fallback routes"),
        "runtime": parser.add_argument_group("State, authentication and limits"),
    }
    help_text = {
        "provider": "Select upstream; explicit URL/model/key still apply",
        "key": "Upstream credential (prefer STOPSLOP_KEY)",
        "policy_file": "Enforcement policy (auto-discovers policy.toml)",
        "classifier": "Semantic assessment backend, configured outside policy",
        "fallback_classifier": "Optional assessment backend on budget exhaustion",
        "policy": "Built-in detector mode when no policy file is configured",
        "rules_file": "Custom detector catalog; cannot be combined with policy-file",
        "deterministic": "Skip semantic checks; keep deterministic enforcement",
        "preserve_model": "Honor client model selection within policy approvals",
        "log_chats": "Record original inputs and delivered replies (default: enabled)",
        "state_file": "SQLite database shared with dashboard and administration",
    }
    for name in Settings.__dataclass_fields__:
        group = ("upstream" if name in ("provider", "base_url", "model", "key") else
                 "fallback" if name.startswith(("fallback_", "local_")) else
                 "enforcement" if name.startswith(("laya_", "jev_")) or name in
                 ("policy", "policy_file", "rules_file", "classifier", "deterministic", "dynamic_policy_file") else "runtime")
        target = groups[group]
        flag = "--" + name.replace("_", "-")
        description = help_text.get(name, "Configure " + name.replace("_", " "))
        description += " (STOPSLOP_" + name.upper() + ")"
        if name in ("deterministic", "preserve_model", "log_chats", "autogen"):
            target.add_argument(flag, action=argparse.BooleanOptionalAction, default=None, help=description)
        else:
            value_type = float if name == "timeout" else int if name in ("max_request_bytes", "max_response_bytes") else str
            target.add_argument(flag, type=value_type, choices=choices.get(name), default=None, help=description)
