"""Detects recurring cash-flow patterns from a user's historical events.

Two shapes show up in the data:
  1. A single (category, description) pair repeating on an almost-monthly
     cadence with a stable amount -- rent, subscriptions, loan instalments,
     salary. Projected forward at the same cadence and last known amount.
  2. A category with many different descriptions and irregular dates but a
     steady overall monthly total -- groceries, transport, dining. Projected
     forward as one monthly lump using the recent per-month average (this is
     the "forecast essential variable spending conservatively" case).
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from ingest.bundle import NormalizedEvent

RECURRING_STATUSES = {"settled", "scheduled"}


def _parse(d: str) -> date:
    return date.fromisoformat(d)


class RecurringTemplate:
    def __init__(self, category, flexibility, cadence_days, last_amount, last_date,
                 reference_event_id, minimum_allowed_amount, direction, source):
        self.category = category
        self.flexibility = flexibility
        self.cadence_days = cadence_days
        self.last_amount = last_amount
        self.last_date = last_date
        self.reference_event_id = reference_event_id
        self.minimum_allowed_amount = minimum_allowed_amount
        self.direction = direction
        self.source = source  # "single_series" | "category_average"

    def project(self, horizon_start: date, horizon_end: date) -> list[tuple[date, Decimal]]:
        occurrences = []
        next_date = self.last_date + timedelta(days=self.cadence_days)
        # advance to the first occurrence on/after horizon_start without
        # accumulating unrealistic backlog if last_date is old
        while next_date < horizon_start:
            next_date += timedelta(days=self.cadence_days)
        while next_date <= horizon_end:
            occurrences.append((next_date, self.last_amount))
            next_date += timedelta(days=self.cadence_days)
        return occurrences


def _resolved_amount(e: NormalizedEvent) -> Decimal | None:
    return e.amount_home


def build_recurring_templates(
    events: list[NormalizedEvent], as_of: date
) -> list[RecurringTemplate]:
    """events: this user's events with amounts already resolved (perception
    has filled in any blank amounts before this is called)."""
    by_series: dict[tuple[str, str], list[NormalizedEvent]] = defaultdict(list)
    for e in events:
        if e.status not in RECURRING_STATUSES:
            continue
        if e.direction not in ("debit", "credit"):
            continue
        if _resolved_amount(e) is None:
            continue
        desc = e.raw.get("description", "")
        by_series[(e.category, desc)].append(e)

    templates: list[RecurringTemplate] = []
    used_categories_single_series: set[str] = set()
    category_pool: dict[str, list[NormalizedEvent]] = defaultdict(list)

    for (category, _desc), series in by_series.items():
        series.sort(key=lambda e: e.event_date)
        category_pool[category].extend(series)
        if len(series) < 2:
            continue
        dates = [_parse(e.event_date) for e in series]
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        if not gaps:
            continue
        median_gap = statistics.median(gaps)
        if median_gap <= 0:
            continue
        spread = max(gaps) - min(gaps) if len(gaps) > 1 else 0
        # accept a monthly-ish or weekly-ish cadence that doesn't jump around wildly
        if median_gap < 5 or median_gap > 40 or spread > median_gap:
            continue
        last = series[-1]
        templates.append(
            RecurringTemplate(
                category=category,
                flexibility=last.flexibility,
                cadence_days=round(median_gap),
                last_amount=_resolved_amount(last),
                last_date=_parse(last.event_date),
                reference_event_id=last.event_id,
                minimum_allowed_amount=last.minimum_allowed_amount,
                direction=last.direction,
                source="single_series",
            )
        )
        used_categories_single_series.add(category)

    # category-level variable-spend average for categories not already
    # covered by a clean single-series template
    for category, evs in category_pool.items():
        if category in used_categories_single_series:
            continue
        # Never synthesize future income/credits from an average -- only
        # project debits (everyday variable spending) this way. Income must
        # come from an explicit repeating series or an explicit future row;
        # see build_income_events in resolve.py.
        if any(e.direction != "debit" for e in evs):
            continue
        evs = sorted(evs, key=lambda e: e.event_date)
        if len(evs) < 2:
            continue
        first_date = _parse(evs[0].event_date)
        last_date = _parse(evs[-1].event_date)
        span_days = max((last_date - first_date).days, 1)
        total = sum((_resolved_amount(e) for e in evs), Decimal("0"))
        monthly_avg = total / (Decimal(span_days) / Decimal(30))
        if monthly_avg <= 0:
            continue
        last = evs[-1]
        templates.append(
            RecurringTemplate(
                category=category,
                flexibility=last.flexibility,
                cadence_days=30,
                last_amount=monthly_avg.quantize(Decimal("0.01")),
                last_date=last_date,
                reference_event_id=last.event_id,
                minimum_allowed_amount=None,
                direction=last.direction,
                source="category_average",
            )
        )

    return templates
