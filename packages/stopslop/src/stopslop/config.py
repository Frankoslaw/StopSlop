from dataclasses import dataclass
import os
from pathlib import Path
from dotenv import load_dotenv

POLICIES = ("block", "redirect", "filter", "passthrough")

@dataclass(frozen=True)
class Settings:
    main_base_url: str = "https://integrate.api.nvidia.com/v1"
    main_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    main_key: str = ""
    policy: str = "block"
    fallback_base_url: str = ""
    fallback_model: str = ""
    fallback_key: str = ""
    rules_file: str = ""
    timeout: float = 120.0

    def __post_init__(self):
        if self.policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        if self.policy == "redirect" and not all((self.fallback_base_url, self.fallback_model, self.fallback_key)):
            raise ValueError("redirect requires fallback URL, model and key")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    @classmethod
    def load(cls, env_file: str = ".env", **overrides):
        load_dotenv(Path(env_file), override=False)
        values = {}
        for name in cls.__dataclass_fields__:
            value = os.getenv("STOPSLOP_" + name.upper())
            if value is not None:
                values[name] = float(value) if name == "timeout" else value
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)
