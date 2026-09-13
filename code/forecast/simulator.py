"""Deterministic day-by-day 90-day balance simulator. No LLM involved --
pure arithmetic over the FinancialFacts the truth layer already resolved.

Core idea: build the day-by-day balance assuming the request is NOT paid at
all (the "base" trajectory). Every other question reduces to arithmetic over
that one array:
  - amount_safe_to_pay: the base trajectory's lowest point tells us exactly
    how much headroom above minimum_balance exists everywhere at once, since
    a payment on day 0 reduces every later day's balance by the same amount.
  - earliest_date_for_full_payment: for each candidate day, the base
    trajectory must already be safe before that day (nothing else broke it),
    and paying in full must not push the *worst* day at-or-after it below
    minimum_balance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from truth.models import FinancialFacts

HORIZON_DAYS = 90


@dataclass
class Forecast:
    request_date: date
    horizon_end: date
    dates: list[date]  # request_date .. horizon_end inclusive
    base_balance: list[Decimal]  # balance on each date, assuming request is never paid
    net_change_by_date: dict[str, Decimal]  # signed net cash change applied ON each date


def build_forecast(facts: FinancialFacts, request_date: date, exclude_categories: set[str] | None = None,
                    override_amounts: dict[str, Decimal] | None = None) -> Forecast:
    """exclude_categories / override_amounts let the decision layer test a
    candidate spending change (stop:<event_id> / reduce_to:<event_id>:<amt>)
    without mutating the shared FinancialFacts."""
    exclude_categories = exclude_categories or set()
    override_amounts = override_amounts or {}

    horizon_end = request_date + timedelta(days=HORIZON_DAYS)
    dates = [request_date + timedelta(days=i) for i in range((horizon_end - request_date).days + 1)]
    net_change: dict[str, Decimal] = {d.isoformat(): Decimal("0") for d in dates}

    for ce in facts.cash_events:
        if ce.date not in net_change:
            continue  # outside horizon
        if ce.reference_event_id in exclude_categories:
            continue
        amount = ce.amount
        if ce.reference_event_id in override_amounts:
            amount = override_amounts[ce.reference_event_id]
        signed = amount if ce.direction == "credit" else -amount
        net_change[ce.date] += signed

    balance = facts.starting_balance
    base_balance = []
    for d in dates:
        balance = balance + net_change[d.isoformat()]
        base_balance.append(balance)

    return Forecast(
        request_date=request_date,
        horizon_end=horizon_end,
        dates=dates,
        base_balance=base_balance,
        net_change_by_date=net_change,
    )


def amount_safe_to_pay(forecast: Forecast, minimum_balance: Decimal, requested_amount: Decimal) -> Decimal:
    min_base = min(forecast.base_balance)
    headroom = min_base - minimum_balance
    safe = max(Decimal("0"), min(headroom, requested_amount))
    return safe.quantize(Decimal("0.01"))


def earliest_date_for_full_payment(forecast: Forecast, minimum_balance: Decimal, requested_amount: Decimal) -> date | None:
    n = len(forecast.dates)
    # suffix_min[i] = min(base_balance[i:])
    suffix_min = [Decimal("0")] * n
    running = None
    for i in range(n - 1, -1, -1):
        running = forecast.base_balance[i] if running is None else min(running, forecast.base_balance[i])
        suffix_min[i] = running

    prefix_ok = True  # base trajectory so far has never broken minimum_balance
    for i in range(n):
        if not prefix_ok:
            break
        if suffix_min[i] - requested_amount >= minimum_balance:
            return forecast.dates[i]
        if forecast.base_balance[i] < minimum_balance:
            prefix_ok = False
    return None
