"""Env-driven configuration — no framework, no host config dependency.

A `.env` file in the working directory is honored (KEY=VALUE lines,
# comments); real environment variables always win."""
from __future__ import annotations

import os
from pathlib import Path

_dotenv = Path(".env")
if _dotenv.exists():
    for line in _dotenv.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Decisions API (structured choice/noul/score answers — no generated prose)
DECISION_MODEL: str = _env("TAXONOMIST_DECISION_MODEL", "openai/gpt-6-luna-decisions")
DECISION_STRUCT_MODEL: str = _env("TAXONOMIST_STRUCT_MODEL", "typesafe/jev-1.13")
DECISION_TEMPERATURE: float = float(_env("TAXONOMIST_DECISION_TEMPERATURE", "0"))
DECISION_SEED: int = int(_env("TAXONOMIST_DECISION_SEED", "7"))
DECISION_TIMEOUT: float = float(_env("TAXONOMIST_DECISION_TIMEOUT_S", "30"))

# Generative API (text-emitting tasks only: suggestions, descriptions)
GEN_MODEL: str = _env("TAXONOMIST_GEN_MODEL", "z-ai/glm-5.3-flash")
GEN_PROVIDERS: list[str] = [
    p.strip() for p in _env("TAXONOMIST_GEN_PROVIDERS", "Wafer,Together,Morph").split(",")
    if p.strip()
]
GEN_TIMEOUT: float = float(_env("TAXONOMIST_GEN_TIMEOUT_S", "60"))


def decisions_url() -> str:
    explicit = os.environ.get("DECISIONS_API_URL")
    if explicit:
        return explicit
    base = OPENROUTER_BASE_URL.rstrip("/")
    if base.endswith("/api/v1"):
        return base[: -len("/api/v1")] + "/api/alpha/decisions"
    return base + "/api/alpha/decisions"
