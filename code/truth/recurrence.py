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


def _add_months(d: date, n: int) -> date:
    """Adds n calendar months to d, keeping the same day-of-month (clamped to
    the target month's last day). A monthly bill due "on the 15th" stays on
    the 15th this way; repeatedly adding a fixed 30-day step instead drifts
    away from the real due date by a few days every cycle over a 90-day
    horizon, which is exactly the kind of date error a strict full-payment
    safety check can't absorb."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


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

    def _is_monthly(self) -> bool:
        return 27 <= self.cadence_days <= 31

    def project(self, horizon_start: date, horizon_end: date) -> list[tuple[date, Decimal]]:
        occurrences = []
        if self._is_monthly():
            k = 1
            next_date = _add_months(self.last_date, k)
            while next_date < horizon_start:
                k += 1
                next_date = _add_months(self.last_date, k)
            while next_date <= horizon_end:
                occurrences.append((next_date, self.last_amount))
                k += 1
                next_date = _add_months(self.last_date, k)
            return occurrences

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
    category_pool: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for e in events:
        if e.status not in RECURRING_STATUSES:
            continue
        if e.direction not in ("debit", "credit"):
            continue
        if _resolved_amount(e) is None:
            continue
        category_pool[e.category].append(e)

    templates: list[RecurringTemplate] = []
    used_categories_single_series: set[str] = set()

    # A category is modeled as ONE clean recurring bill/income only when it is
    # backed by at most 2 distinct descriptions -- rent, subscriptions, loan
    # instalments are always exactly one; salary shows up as "Prorated first
    # salary" then "Next confirmed salary" (still one paycheck a month, just
    # renamed once). A category spread across 3+ different descriptions
    # (groceries: "Neighbourhood grocer", "Bulk pantry shop", "Grocery
    # delivery", ...) is many independent everyday purchases, not a bill --
    # pooling all of those together can *coincidentally* look cadence-regular
    # (busy categories with many events naturally have low date-gap spread),
    # so the description-count gate has to come before the cadence check, not
    # rely on the cadence check alone to tell them apart.
    for category, series in category_pool.items():
        distinct_descriptions = {e.raw.get("description", "") for e in series}
        if len(distinct_descriptions) > 2:
            continue
        series = sorted(series, key=lambda e: e.event_date)
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
        # The template's amount must come from the DOMINANT description's
        # last instance, not just whichever event happens to be chronologically
        # last -- a one-off "Outstanding rent balance" arrears payment tacked
        # onto 6 months of "Monthly rent" is still the same category and would
        # otherwise silently become the projected monthly rent amount, which
        # is exactly the kind of one-time event this is supposed to exclude.
        # Ties (e.g. salary renamed once: "Prorated first salary" then "Next
        # confirmed salary", one instance each) fall back to the most recent
        # description, since that's the one still in effect going forward.
        by_desc: dict[str, list] = defaultdict(list)
        for e in series:
            by_desc[e.raw.get("description", "")].append(e)
        dominant_desc = max(
            by_desc,
            key=lambda d: (len(by_desc[d]), by_desc[d][-1].event_date),
        )
        last = by_desc[dominant_desc][-1]
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
        # Averaging a credit category as if it recurs would invent income for
        # a one-off refund/bonus/investment payout -- except "salary", which
        # this dataset also uses for freelance/gig income billed under a
        # different description every time (each payment a different client
        # or contract name) but on an evidenced, regular cadence. That's real
        # earned income, just not literally one employer -- averaging it is
        # forecasting evidence, not inventing it.
        if any(e.direction != "debit" for e in evs) and category != "salary":
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
        # Real spending in these categories happens as several purchases a
        # month, not one lump sum. Projecting the whole monthly average as a
        # single debit creates an artificial one-day dip in the balance
        # simulator that wouldn't happen with the real, spread-out purchases
        # -- so split it into ~3 roughly-10-day instalments of the average
        # instead of one 30-day lump.
        installments_per_month = 3
        last = evs[-1]
        templates.append(
            RecurringTemplate(
                category=category,
                flexibility=last.flexibility,
                cadence_days=30 // installments_per_month,
                last_amount=(monthly_avg / installments_per_month).quantize(Decimal("0.01")),
                last_date=last_date,
                reference_event_id=last.event_id,
                minimum_allowed_amount=None,
                direction=last.direction,
                source="category_average",
            )
        )

    return templates
