"""Decimal-based money helpers. Everything downstream of ingest works in the
user's home_currency and in Decimal, never float, so 90 days of running
balance arithmetic doesn't drift."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def to_decimal(value, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return default


def round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt(value: Decimal) -> str:
    """Renders a Decimal the way the sample output does: plain digits, no
    scientific notation, no forced trailing zeros (25256, not 25256.00)."""
    s = format(round2(value), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s
