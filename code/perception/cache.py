"""Disk cache for perception extraction results, keyed by message_id/image_id.

Groq calls cost real tokens and the rest of the pipeline (truth, forecast,
decide) gets iterated on far more often than the raw message/image text
changes. Without this, every dev run of main.py would re-classify all ~230
messages/images from scratch. The cache is local build output, not part of
the submitted solution logic -- deleting it just forces re-extraction.
"""
from __future__ import annotations

import json
from pathlib import Path


class JsonCache:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._data = {}

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        self._data[key] = value

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
