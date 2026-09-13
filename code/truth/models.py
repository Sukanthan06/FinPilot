"""Data shapes produced by the truth layer and consumed by forecast/decide.
Pure data -- no logic lives here."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class CashEvent:
    """One dated cash movement the forecast simulator must apply."""
    date: str  # YYYY-MM-DD
    amount: Decimal  # always positive; direction says which way it moves the balance
    direction: str  # "debit" or "credit"
    category: str
    flexibility: str  # fixed | reducible | stoppable | reducible_or_stoppable
    reference_event_id: str  # event_id to cite in spending_changes_needed
    origin: str  # settled_future | scheduled | pending_debit | recurring_projection | message_signal
    minimum_allowed_amount: Decimal | None = None


@dataclass
class FinancialFacts:
    user_id: str
    home_currency: str
    starting_balance: Decimal
    minimum_balance: Decimal
    financial_priorities: list[str]
    protect_categories: set[str]
    reduce_categories: set[str]
    stop_categories: set[str]
    payment_methods_accepted: set[str]
    max_installment_months: int | None
    cash_events: list[CashEvent] = field(default_factory=list)  # forward-looking, from request_date
    notes: list[str] = field(default_factory=list)  # human-readable provenance for decision_explanation
