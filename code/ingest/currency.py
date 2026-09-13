"""Currency normalization to each user's home_currency.

Every checked case in this dataset has an exact (rate_date, from, to) row for
the event's settlement date (see NOTES.md), so exact match is the primary
path. A nearest-earlier-date fallback within the same currency pair exists
only as a safety net for rows outside that checked set — it never invents a
pair that isn't in exchange_rates.csv.
"""
from __future__ import annotations

from bisect import bisect_right
from decimal import Decimal

from common.money import to_decimal


class RateTable:
    def __init__(self, rate_rows: list[dict]):
        self._exact: dict[tuple[str, str, str], Decimal] = {}
        self._by_pair: dict[tuple[str, str], list[tuple[str, Decimal]]] = {}
        for r in rate_rows:
            key = (r["rate_date"], r["from_currency"], r["to_currency"])
            rate = to_decimal(r["rate"])
            self._exact[key] = rate
            self._by_pair.setdefault((r["from_currency"], r["to_currency"]), []).append(
                (r["rate_date"], rate)
            )
        for pair, rows in self._by_pair.items():
            rows.sort(key=lambda t: t[0])

    def rate(self, date: str, from_ccy: str, to_ccy: str) -> Decimal | None:
        if from_ccy == to_ccy:
            return Decimal("1")
        exact = self._exact.get((date, from_ccy, to_ccy))
        if exact is not None:
            return exact
        rows = self._by_pair.get((from_ccy, to_ccy))
        if not rows:
            return None
        dates = [d for d, _ in rows]
        idx = bisect_right(dates, date) - 1
        if idx < 0:
            return None
        return rows[idx][1]

    def convert(self, amount: Decimal, date: str, from_ccy: str, to_ccy: str) -> Decimal | None:
        r = self.rate(date, from_ccy, to_ccy)
        if r is None:
            return None
        return amount * r
