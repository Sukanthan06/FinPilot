"""Minimal .env loader (case-insensitive keys) so we don't need a third-party
dependency just to read GROQ_API_KEY. Values already in the real environment
take precedence over the .env file."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().upper()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get_groq_api_key() -> str | None:
    return os.environ.get("GROQ_API_KEY")
