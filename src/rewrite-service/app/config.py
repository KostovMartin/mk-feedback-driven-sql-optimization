from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    target_db_connection: str
    enable_rules: bool
    enable_llm: bool
    model_provider: str
    ollama_url: str
    default_model: str
    temperature: float


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value is not None else default


settings = Settings(
    target_db_connection=os.environ.get(
        "TARGET_DB_CONNECTION",
        "postgresql://postgres:postgres@localhost:5432/tpch",
    ),
    enable_rules=_bool_env("ENABLE_RULES", True),
    enable_llm=_bool_env("ENABLE_LLM", False),
    model_provider=os.environ.get("MODEL_PROVIDER", "ollama"),
    ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
    default_model=os.environ.get("DEFAULT_MODEL", ""),
    temperature=_float_env("TEMPERATURE", 0.7),
)
