"""Thin Groq chat-completions wrapper used by every LLM call in the pipeline
(perception extraction here, explanation generation in explain/). Tracks
token usage per call so evaluation/usage_report.md can be built from real
numbers instead of estimates.

Model choice (checked live against GET /openai/v1/models on 2026-09-13 --
llama-3.3-70b-versatile and the old llama-3.2 vision-preview models are gone
from Groq's lineup by now):
  - TEXT_MODEL:  openai/gpt-oss-120b   -- strong instruction following, JSON mode
  - VISION_MODEL: qwen/qwen3.8-27b     -- confirmed to read amounts off the
                                          dataset's payslip/receipt images,
                                          answers directly instead of a long
                                          <think> block (qwen3.6-27b is a
                                          reasoning variant that burns tokens
                                          narrating instead of answering)
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TEXT_MODEL = "openai/gpt-oss-120b"
VISION_MODEL = "qwen/qwen3.8-27b"


@dataclass
class UsageLog:
    calls: list[dict] = field(default_factory=list)

    def record(self, model: str, purpose: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.calls.append(
            {
                "model": model,
                "purpose": purpose,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        )


class GroqClient:
    def __init__(self, api_key: str | None = None, usage: UsageLog | None = None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.usage = usage if usage is not None else UsageLog()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _post(self, payload: dict, purpose: str, retries: int = 3) -> dict | None:
        if not self.api_key:
            return None
        last_err = None
        for attempt in range(retries):
            try:
                r = requests.post(GROQ_URL, headers=self._headers(), json=payload, timeout=60)
                if r.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                data = r.json()
                usage = data.get("usage", {})
                self.usage.record(
                    payload["model"],
                    purpose,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )
                return data
            except Exception as e:  # network hiccup, 5xx, etc. -- retry, then give up
                last_err = e
                time.sleep(1 + attempt)
        if last_err:
            print(f"[groq_client] giving up on {purpose} after {retries} attempts: {last_err}")
        return None

    def extract_json(self, system_prompt: str, user_content: str, purpose: str, max_tokens: int = 400) -> dict | None:
        payload = {
            "model": TEXT_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        data = self._post(payload, purpose)
        return _parse_json_response(data)

    def extract_json_from_image(
        self, system_prompt: str, user_text: str, image_path: Path, purpose: str, max_tokens: int = 300
    ) -> dict | None:
        b64 = base64.b64encode(image_path.read_bytes()).decode()
        data_url = f"data:image/png;base64,{b64}"
        payload = {
            "model": VISION_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        data = self._post(payload, purpose)
        return _parse_json_response(data)


def _parse_json_response(data: dict | None) -> dict | None:
    if not data:
        return None
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return None
    content = content.strip()
    # Some models wrap JSON in ```json fences or leave stray <think> text
    # despite response_format=json_object; salvage the first {...} block.
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
