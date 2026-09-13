"""Defends the decision layer against prompt injection carried in messages.csv
or the text/vision output derived from images.csv.

Two independent layers:
1. sanitize_text() strips or neutralizes obvious instruction-like spans
   before the text is ever placed in a Groq prompt (defense in depth, not a
   substitute for #2).
2. The system prompts in groq_client.py tell the model explicitly that the
   content is untrusted data to extract facts FROM, never instructions to
   follow, and force JSON-schema-only output so there is no free-text
   channel for a directive to act through.

Nothing here ever executes a directive found in message/image content. The
only thing this module is allowed to produce is either a cleaned string or a
boolean flag; it never returns a decision, an amount override, or a config
change.
"""
from __future__ import annotations

import re

# Patterns that look like an attempt to redirect the model's behavior rather
# than describe a financial fact. Case-insensitive, matched anywhere in the
# text.
_INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) instructions",
    r"disregard (the |all )?(rules|instructions|policy)",
    r"you are now",
    r"new instructions?:",
    r"system\s*:",
    r"assistant\s*:",
    r"act as",
    r"pretend (to be|you are)",
    r"override (the )?(rules|policy|decision)",
    r"approve (this|the) (request|payment|purchase)",
    r"set (amount_safe_to_pay|affordability_status|recommended_payment_method)",
    r"do not (check|verify|apply) (the )?(minimum balance|safety check)",
    r"reveal (your|the) (prompt|system prompt|instructions)",
]
_COMPILED = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def contains_injection_attempt(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _COMPILED)


def sanitize_text(text: str) -> str:
    """Returns the text with instruction-like spans replaced by a neutral
    marker, so any residual influence on the model is descriptive
    ("the message tried to give an instruction here") rather than
    imperative."""
    if not text:
        return text
    cleaned = text
    for pattern in _COMPILED:
        cleaned = pattern.sub("[redacted instruction-like text]", cleaned)
    return cleaned
