"""Enumerates every eligible candidate plan and ranks them with the exact
6-rule tie-break from the spec, applied as sequential filters/sort keys.
Produces the final Decision -- explain/ only narrates it afterward, decide/
is where every number and status is actually chosen."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from forecast.simulator import Forecast, amount_safe_to_pay, build_forecast, earliest_date_for_full_payment
from decide.spending_changes import SpendingChangeOption, find_minimal_spending_changes
from truth.models import FinancialFacts

BIG_DATE = date(9999, 12, 31)
BIG_ID = "zzzzzzzz"


@dataclass
class Candidate:
    method: str
    status: str
    amount_safe_to_pay: Decimal
    payments: list[tuple[date, Decimal]]  # chronological
    spending_changes: list[SpendingChangeOption] = field(default_factory=list)
    payment_option_id: str | None = None

    @property
    def total_paid(self) -> Decimal:
        return sum((amt for _, amt in self.payments), Decimal("0"))

    @property
    def completion_date(self) -> date:
        return max((d for d, _ in self.payments), default=BIG_DATE)

    @property
    def start_date(self) -> date:
        return min((d for d, _ in self.payments), default=BIG_DATE)

    @property
    def num_payments(self) -> int:
        return len(self.payments)


@dataclass
class Decision:
    amount_safe_to_pay: Decimal
    affordability_status: str
    recommended_payment_method: str
    payments: list[tuple[date, Decimal]]
    earliest_date_for_full_payment: date | None
    spending_changes: list[SpendingChangeOption]
    winning_candidate: Candidate | None
    base_forecast: Forecast


def _months_span(number_of_payments: int, frequency_days) -> Decimal:
    if not frequency_days or number_of_payments <= 1:
        return Decimal("0")
    return (Decimal(number_of_payments - 1) * Decimal(str(frequency_days))) / Decimal("30")


def _installment_plan_from_option(option: dict) -> list[tuple[date, Decimal]]:
    n = int(option["number_of_payments"])
    amount = Decimal(str(option["payment_amount"]))
    start = date.fromisoformat(option["first_payment_date"])
    freq = int(option["payment_frequency_days"]) if option.get("payment_frequency_days") else 0
    return [(start + timedelta(days=freq * i), amount) for i in range(n)]


def _plan_is_safe(facts: FinancialFacts, request_date: date, payments: list[tuple[date, Decimal]]) -> bool:
    forecast = build_forecast(facts, request_date)
    net = dict(forecast.net_change_by_date)
    for d, amt in payments:
        key = d.isoformat()
        if key not in net:
            return False  # payment falls outside the 90-day forecast horizon
        net[key] -= amt
    balance = facts.starting_balance
    for d in forecast.dates:
        balance += net[d.isoformat()]
        if balance < facts.minimum_balance:
            return False
    return True


def build_candidates(
    facts: FinancialFacts, request: dict, payment_options: list[dict], base_forecast: Forecast
) -> list[Candidate]:
    request_date = date.fromisoformat(request["request_date"])
    desired_completion = date.fromisoformat(request["desired_completion_date"])
    requested_amount = Decimal(str(request["requested_amount"]))
    allows_partial = str(request.get("allows_partial_payment", "")).strip().lower() == "true"

    safe0 = amount_safe_to_pay(base_forecast, facts.minimum_balance, requested_amount)
    earliest0 = earliest_date_for_full_payment(base_forecast, facts.minimum_balance, requested_amount)

    candidates: list[Candidate] = []

    if "full_payment" in facts.payment_methods_accepted:
        if safe0 >= requested_amount and earliest0 == request_date:
            candidates.append(
                Candidate(
                    method="full_payment",
                    status="affordable_now",
                    amount_safe_to_pay=safe0,
                    payments=[(request_date, requested_amount)],
                )
            )
        else:
            changes = find_minimal_spending_changes(facts, request_date, requested_amount)
            if changes is not None:
                candidates.append(
                    Candidate(
                        method="full_payment",
                        status="affordable_with_plan",
                        amount_safe_to_pay=safe0,
                        payments=[(request_date, requested_amount)],
                        spending_changes=changes,
                    )
                )
        if earliest0 is not None and earliest0 != request_date:
            candidates.append(
                Candidate(
                    method="wait",
                    status="affordable_later",
                    amount_safe_to_pay=safe0,
                    payments=[(earliest0, requested_amount)],
                )
            )

    if (
        allows_partial
        and "partial_payment" in facts.payment_methods_accepted
        and 0 < safe0 < requested_amount
        and earliest0 is not None
        and earliest0 <= desired_completion
    ):
        candidates.append(
            Candidate(
                method="partial_payment",
                status="affordable_with_plan",
                amount_safe_to_pay=safe0,
                payments=[(request_date, safe0), (earliest0, (requested_amount - safe0).quantize(Decimal("0.01")))],
            )
        )

    if "installments" in facts.payment_methods_accepted:
        for option in payment_options:
            if option.get("payment_method") != "installments":
                continue
            if facts.max_installment_months is None:
                continue  # blank max_installment_months means installments are not considered at all
            if _months_span(int(option["number_of_payments"]), option.get("payment_frequency_days")) > facts.max_installment_months:
                continue
            plan = _installment_plan_from_option(option)
            if not _plan_is_safe(facts, request_date, plan):
                continue
            status = "affordable_now" if len(plan) == 1 and plan[0][0] == request_date else "affordable_with_plan"
            candidates.append(
                Candidate(
                    method="installments",
                    status=status,
                    amount_safe_to_pay=safe0,
                    payments=plan,
                    payment_option_id=option["payment_option_id"],
                )
            )

    return candidates


def _sort_key(c: Candidate, desired_completion: date):
    on_time = 0 if c.completion_date <= desired_completion else 1
    no_changes = 0 if not c.spending_changes else 1
    option_key = c.payment_option_id or BIG_ID
    return (on_time, no_changes, c.total_paid, c.start_date, c.num_payments, option_key)


def decide(facts: FinancialFacts, request: dict, payment_options: list[dict]) -> Decision:
    request_date = date.fromisoformat(request["request_date"])
    desired_completion = date.fromisoformat(request["desired_completion_date"])
    requested_amount = Decimal(str(request["requested_amount"]))

    base_forecast = build_forecast(facts, request_date)
    safe0 = amount_safe_to_pay(base_forecast, facts.minimum_balance, requested_amount)
    earliest0 = earliest_date_for_full_payment(base_forecast, facts.minimum_balance, requested_amount)

    candidates = build_candidates(facts, request, payment_options, base_forecast)

    if not candidates:
        return Decision(
            amount_safe_to_pay=safe0,
            affordability_status="not_affordable",
            recommended_payment_method="not_recommended",
            payments=[],
            earliest_date_for_full_payment=earliest0,
            spending_changes=[],
            winning_candidate=None,
            base_forecast=base_forecast,
        )

    candidates.sort(key=lambda c: _sort_key(c, desired_completion))
    winner = candidates[0]

    return Decision(
        amount_safe_to_pay=safe0,
        affordability_status=winner.status,
        recommended_payment_method=winner.method,
        payments=winner.payments,
        earliest_date_for_full_payment=earliest0,
        spending_changes=winner.spending_changes,
        winning_candidate=winner,
        base_forecast=base_forecast,
    )
