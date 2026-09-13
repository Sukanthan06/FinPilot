"""Strict JSON schemas that every perception extraction must conform to.
The decision layers only ever see these typed objects, never raw model text."""
from __future__ import annotations

from dataclasses import dataclass

SIGNAL_TYPES = {
    "income_new_or_increase",
    "income_decrease",
    "expense_new",
    "expense_cancelled",
    "expense_amended",
    "payment_delayed",
    "event_confirmed",
    "none",
}


@dataclass
class MessageSignal:
    source_id: str  # message_id
    signal_type: str  # one of SIGNAL_TYPES
    related_event_id: str | None
    category: str | None
    amount: float | None
    currency: str | None
    effective_date: str | None  # YYYY-MM-DD
    confidence: float
    is_newer_than_record: bool  # true if the message postdates the event/profile record it touches
    injection_flagged: bool


@dataclass
class ImageAmountSignal:
    source_id: str  # image_id
    related_event_id: str
    amount: float | None
    currency: str | None
    date: str | None
    confidence: float
