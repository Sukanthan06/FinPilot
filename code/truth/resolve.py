"""Deterministic conflict resolution: turns (normalized events + perception
signals) into the FinancialFacts the forecast simulator needs. No LLM calls
happen in this module -- perception has already run, this only applies the
priority order from the spec:

  1. explicit cancellation, settlement, or amendment
  2. a newer record from the same source
  3. a settled event over an estimate/forecast
  4. the financially safer interpretation when it still can't be resolved

and then projects forward: recurring templates + explicit future-dated rows,
capped to the 90-day forecast horizon from the request date.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from ingest.bundle import NormalizedEvent, RequestBundle
from ingest.currency import RateTable
from perception.schema import ImageAmountSignal, MessageSignal
from truth.models import CashEvent, FinancialFacts
from truth.recurrence import build_recurring_templates

FORECAST_HORIZON_DAYS = 90
EXCLUDED_STATUSES = {"cancelled", "failed", "unrealized"}
INCOME_EVENT_TYPES = {"income"}


def _pipe_set(value: str) -> set[str]:
    return {v.strip() for v in (value or "").split("|") if v.strip()}


def _resolve_blank_amounts(
    events: list[NormalizedEvent],
    image_signals: dict[str, ImageAmountSignal],
    rates: RateTable,
    home_currency: str,
) -> list[NormalizedEvent]:
    resolved = []
    for e in events:
        if not e.amount_unresolved:
            resolved.append(e)
            continue
        sig = image_signals.get(e.event_id)
        if sig is None or sig.amount is None:
            resolved.append(e)  # stays unresolved; excluded from forecast below
            continue
        amount = Decimal(str(sig.amount))
        currency = sig.currency or e.currency
        settle_date = sig.date or e.settlement_date
        amount_home = rates.convert(amount, settle_date, currency, home_currency)
        resolved.append(
            replace(e, amount_home=amount_home, amount_unresolved=amount_home is None)
        )
    return resolved


def _apply_message_amendments(
    events: list[NormalizedEvent], messages: list[MessageSignal]
) -> tuple[list[NormalizedEvent], list[str]]:
    """Explicit cancellation/amendment/delay from a message always wins over
    the original record (priority #1 in the spec)."""
    by_id = {e.event_id: e for e in events}
    notes: list[str] = []
    for sig in messages:
        if sig.injection_flagged:
            notes.append(f"message {sig.source_id} contained instruction-like text; ignored as a directive")
        if not sig.related_event_id or sig.related_event_id not in by_id:
            continue
        target = by_id[sig.related_event_id]
        if sig.signal_type == "expense_cancelled":
            by_id[sig.related_event_id] = replace(target, status="cancelled")
            notes.append(f"{sig.related_event_id} cancelled per message {sig.source_id}")
        elif sig.signal_type == "expense_amended" and sig.amount is not None:
            by_id[sig.related_event_id] = replace(target, amount_home=Decimal(str(sig.amount)))
            notes.append(f"{sig.related_event_id} amount amended to {sig.amount} per message {sig.source_id}")
        elif sig.signal_type == "payment_delayed" and sig.effective_date:
            by_id[sig.related_event_id] = replace(
                target, event_date=sig.effective_date, settlement_date=sig.effective_date
            )
            notes.append(f"{sig.related_event_id} delayed to {sig.effective_date} per message {sig.source_id}")
        elif sig.signal_type == "event_confirmed":
            notes.append(f"{sig.related_event_id} confirmed per message {sig.source_id}")
    return list(by_id.values()), notes


def _dedupe(events: list[NormalizedEvent]) -> list[NormalizedEvent]:
    seen = set()
    out = []
    for e in events:
        key = (e.category, e.raw.get("description", ""), e.direction, e.event_date, str(e.amount_home))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _income_adjustment(messages: list[MessageSignal]) -> tuple[Decimal | None, str | None]:
    """A payroll-style message with no related_event_id describes a change to
    the user's ongoing salary, not one historical row. Returns the latest
    (by effective_date) new amount and the date it takes effect, if any."""
    candidates = [
        s for s in messages
        if s.related_event_id is None
        and s.signal_type in ("income_new_or_increase", "income_decrease")
        and s.amount is not None
        and s.effective_date
    ]
    if not candidates:
        return None, None
    latest = max(candidates, key=lambda s: s.effective_date)
    return Decimal(str(latest.amount)), latest.effective_date


def build_financial_facts(
    bundle: RequestBundle,
    message_signals: dict[str, MessageSignal],
    image_signals: dict[str, ImageAmountSignal],
    rates: RateTable,
) -> FinancialFacts:
    profile = bundle.profile
    home_currency = profile["home_currency"]
    request_date = date.fromisoformat(bundle.request["request_date"])
    horizon_end = request_date + timedelta(days=FORECAST_HORIZON_DAYS)

    msg_signals_for_user = [message_signals[m["message_id"]] for m in bundle.messages if m["message_id"] in message_signals]

    events = _resolve_blank_amounts(bundle.events, image_signals, rates, home_currency)
    events, amend_notes = _apply_message_amendments(events, msg_signals_for_user)
    events = [e for e in events if e.status not in EXCLUDED_STATUSES]
    events = [e for e in events if e.amount_home is not None]  # drop still-unresolved amounts rather than invent one
    events = _dedupe(events)

    # pending credits never count until settled; pending debits are reserved
    events = [e for e in events if not (e.direction == "credit" and e.status == "pending")]

    facts = FinancialFacts(
        user_id=profile["user_id"],
        home_currency=home_currency,
        starting_balance=Decimal(str(profile["current_available_balance"])),
        minimum_balance=Decimal(str(profile["minimum_balance_to_keep"])),
        financial_priorities=list(_pipe_set(profile.get("financial_priorities", ""))),
        protect_categories=_pipe_set(profile.get("expense_categories_to_protect", "")),
        reduce_categories=_pipe_set(profile.get("expense_categories_user_is_willing_to_reduce", "")),
        stop_categories=_pipe_set(profile.get("expense_categories_user_is_willing_to_stop", "")),
        payment_methods_accepted=_pipe_set(profile.get("payment_methods_user_will_consider", "")),
        max_installment_months=int(profile["max_installment_months"]) if profile.get("max_installment_months") else None,
        notes=amend_notes,
    )

    # 1) explicit future-dated rows already in the data: scheduled anything,
    #    and pending debits (reserved regardless of date).
    for e in events:
        e_date = date.fromisoformat(e.event_date)
        is_future_scheduled = e.status == "scheduled" and e_date >= request_date
        is_pending_debit = e.status == "pending" and e.direction == "debit"
        if not (is_future_scheduled or is_pending_debit):
            continue
        due_date = e_date if e_date >= request_date else request_date
        if due_date > horizon_end:
            continue
        facts.cash_events.append(
            CashEvent(
                date=due_date.isoformat(),
                amount=e.amount_home,
                direction=e.direction,
                category=e.category,
                flexibility=e.flexibility,
                reference_event_id=e.event_id,
                origin="scheduled" if is_future_scheduled else "pending_debit",
                minimum_allowed_amount=e.minimum_allowed_amount,
            )
        )

    # 2) recurring templates projected across the horizon (debits: bills +
    #    variable-spend averages; credits: only a clean repeating series,
    #    never a synthesized average -- see recurrence.py).
    templates = build_recurring_templates(events, as_of=request_date)
    for tmpl in templates:
        for occ_date, amount in tmpl.project(request_date, horizon_end):
            facts.cash_events.append(
                CashEvent(
                    date=occ_date.isoformat(),
                    amount=amount,
                    direction=tmpl.direction,
                    category=tmpl.category,
                    flexibility=tmpl.flexibility,
                    reference_event_id=tmpl.reference_event_id,
                    origin="recurring_projection",
                    minimum_allowed_amount=tmpl.minimum_allowed_amount,
                )
            )

    # 3) a payroll message describing an ongoing salary change overrides the
    #    amount of any recurring-salary projection from its effective date
    #    onward (priority #1: explicit amendment beats the historical record).
    new_income_amount, effective_date = _income_adjustment(msg_signals_for_user)
    if new_income_amount is not None and effective_date:
        for ce in facts.cash_events:
            if ce.category == "salary" and ce.origin == "recurring_projection" and ce.date >= effective_date:
                ce.amount = new_income_amount
        facts.notes.append(f"salary updated to {new_income_amount} effective {effective_date} per message")

    facts.cash_events.sort(key=lambda ce: ce.date)
    return facts
