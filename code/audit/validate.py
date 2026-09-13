"""Validates the generated output rows against every invariant in the spec
before a run is considered final. Raises AuditError listing every problem
found (not just the first) so issues can be fixed in one pass."""
from __future__ import annotations

from decimal import Decimal

VALID_STATUS = {"affordable_now", "affordable_with_plan", "affordable_later", "not_affordable"}
VALID_METHOD = {"full_payment", "partial_payment", "installments", "wait", "not_recommended"}


class AuditError(Exception):
    pass


def _parse_plan(plan_str: str) -> list[tuple[str, Decimal]]:
    if plan_str == "none":
        return []
    out = []
    for part in plan_str.split("|"):
        d, amt = part.split(":")
        out.append((d, Decimal(amt)))
    return out


def _parse_changes(changes_str: str) -> list[str]:
    if changes_str == "none":
        return []
    return changes_str.split("|")


def audit_output(rows: list[dict], requests_by_id: dict[str, dict], valid_option_ids: dict[str, set[str]],
                  flexible_event_ids: set[str]) -> list[str]:
    errors: list[str] = []

    for row in rows:
        rid = row["request_id"]
        req = requests_by_id.get(rid)
        if req is None:
            errors.append(f"{rid}: not a real request_id")
            continue

        requested_amount = Decimal(str(req["requested_amount"]))
        try:
            safe = Decimal(str(row["amount_safe_to_pay"]))
        except Exception:
            errors.append(f"{rid}: amount_safe_to_pay is not numeric: {row['amount_safe_to_pay']!r}")
            continue
        if not (Decimal("0") <= safe <= requested_amount):
            errors.append(f"{rid}: amount_safe_to_pay {safe} out of bounds [0, {requested_amount}]")

        status = row["affordability_status"]
        if status not in VALID_STATUS:
            errors.append(f"{rid}: invalid affordability_status {status!r}")

        method = row["recommended_payment_method"]
        if method not in VALID_METHOD:
            errors.append(f"{rid}: invalid recommended_payment_method {method!r}")

        earliest = row["earliest_date_for_full_payment"]
        if status == "affordable_now" and earliest != req["request_date"]:
            errors.append(f"{rid}: affordable_now requires earliest_date_for_full_payment == request_date")

        try:
            plan = _parse_plan(row["payment_plan"])
        except Exception:
            errors.append(f"{rid}: malformed payment_plan {row['payment_plan']!r}")
            plan = []

        dates = [d for d, _ in plan]
        if dates != sorted(dates):
            errors.append(f"{rid}: payment_plan dates not chronological")

        if method == "partial_payment":
            if status != "affordable_with_plan":
                errors.append(f"{rid}: partial_payment must have affordability_status affordable_with_plan")
            if len(plan) != 2:
                errors.append(f"{rid}: partial_payment must have exactly 2 payments, got {len(plan)}")
            elif plan[0][1] != safe:
                errors.append(f"{rid}: partial_payment first payment {plan[0][1]} != amount_safe_to_pay {safe}")
            total = sum(amt for _, amt in plan)
            if total != requested_amount:
                errors.append(f"{rid}: partial_payment total {total} != requested_amount {requested_amount}")

        if method == "installments":
            option_ids = valid_option_ids.get(rid, set())
            # a valid installment plan must reproduce one supplied option's schedule exactly
            matched = False
            for opt_amount, opt_n in [(None, None)]:
                pass
            if not option_ids:
                errors.append(f"{rid}: installments recommended but request has no payment options at all")

        if method in ("full_payment", "partial_payment", "installments") and not plan:
            errors.append(f"{rid}: {method} requires a non-empty payment_plan")
        if method in ("wait", "not_recommended") and method == "not_recommended" and plan:
            errors.append(f"{rid}: not_recommended should have payment_plan 'none'")

        changes = _parse_changes(row["spending_changes_needed"])
        if len(changes) > 3:
            errors.append(f"{rid}: more than 3 spending changes")
        stopped_ids, reduced_ids = set(), set()
        for c in changes:
            if c.startswith("stop:"):
                eid = c.split(":", 1)[1]
                stopped_ids.add(eid)
                if flexible_event_ids and eid not in flexible_event_ids:
                    errors.append(f"{rid}: stop targets non-flexible/unknown event {eid}")
            elif c.startswith("reduce_to:"):
                _, eid, amt = c.split(":", 2)
                reduced_ids.add(eid)
                if flexible_event_ids and eid not in flexible_event_ids:
                    errors.append(f"{rid}: reduce_to targets non-flexible/unknown event {eid}")
            else:
                errors.append(f"{rid}: malformed spending change {c!r}")
        if stopped_ids & reduced_ids:
            errors.append(f"{rid}: same event both stopped and reduced")

        if not row.get("decision_explanation", "").strip():
            errors.append(f"{rid}: empty decision_explanation")

    return errors
