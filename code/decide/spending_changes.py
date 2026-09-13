"""Finds the smallest combination of permitted spending changes (stop /
reduce_to a flexible recurring category) that makes the full requested
amount safe to pay today. Pure search over a small candidate set -- no LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import combinations

from forecast.simulator import amount_safe_to_pay, build_forecast
from truth.models import FinancialFacts


@dataclass
class SpendingChangeOption:
    kind: str  # "stop" or "reduce_to"
    reference_event_id: str
    category: str
    new_amount: Decimal | None  # None for stop
    disruption: Decimal  # amount removed from cash flow, for ranking minimal changes first

    def label(self) -> str:
        if self.kind == "stop":
            return f"stop:{self.reference_event_id}"
        return f"reduce_to:{self.reference_event_id}:{self.new_amount}"


def eligible_spending_change_options(facts: FinancialFacts) -> list[SpendingChangeOption]:
    options: list[SpendingChangeOption] = []
    seen_events: set[str] = set()
    for ce in facts.cash_events:
        if ce.direction != "debit":
            continue
        if ce.category in facts.protect_categories:
            continue
        if ce.reference_event_id in seen_events:
            continue
        can_stop = ce.flexibility in ("stoppable", "reducible_or_stoppable") and ce.category in facts.stop_categories
        can_reduce = (
            ce.flexibility in ("reducible", "reducible_or_stoppable")
            and ce.category in facts.reduce_categories
            and ce.minimum_allowed_amount is not None
        )
        if not can_stop and not can_reduce:
            continue
        seen_events.add(ce.reference_event_id)
        if can_stop:
            options.append(
                SpendingChangeOption(
                    kind="stop",
                    reference_event_id=ce.reference_event_id,
                    category=ce.category,
                    new_amount=None,
                    disruption=ce.amount,
                )
            )
        if can_reduce:
            saved = ce.amount - ce.minimum_allowed_amount
            if saved > 0:
                options.append(
                    SpendingChangeOption(
                        kind="reduce_to",
                        reference_event_id=ce.reference_event_id,
                        category=ce.category,
                        new_amount=ce.minimum_allowed_amount,
                        disruption=saved,
                    )
                )
    return options


def find_minimal_spending_changes(
    facts: FinancialFacts, request_date: date, requested_amount: Decimal, max_changes: int = 3
) -> list[SpendingChangeOption] | None:
    options = sorted(eligible_spending_change_options(facts), key=lambda o: -o.disruption)
    for size in range(1, max_changes + 1):
        for combo in combinations(options, size):
            exclude = {o.reference_event_id for o in combo if o.kind == "stop"}
            overrides = {o.reference_event_id: o.new_amount for o in combo if o.kind == "reduce_to"}
            forecast = build_forecast(facts, request_date, exclude_categories=exclude, override_amounts=overrides)
            safe = amount_safe_to_pay(forecast, facts.minimum_balance, requested_amount)
            if safe >= requested_amount:
                return list(combo)
    return None
