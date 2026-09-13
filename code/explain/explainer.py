"""Writes decision_explanation from the already-computed Decision. The model
only narrates numbers it is given -- it cannot alter amount_safe_to_pay,
affordability_status, recommended_payment_method, payment_plan, or
spending_changes_needed, because those are formatted into the prompt as
fixed facts, not something it is asked to recompute. A short deterministic
template is used as a fallback if Groq is unavailable, so the pipeline never
produces an empty explanation."""
from __future__ import annotations

from decimal import Decimal

from decide.engine import Decision
from perception.groq_client import GroqClient

SYSTEM_PROMPT = (
    "You write a one-to-two sentence explanation of a financial affordability "
    "decision for the end user. You are given the final decision and the "
    "financial facts behind it as already-computed values -- you must not "
    "change, recompute, or contradict any number or status given to you. "
    "Do not invent facts not present in the input. Be concise, concrete, and "
    "reference the actual amounts and dates given. "
    'Respond with exactly this JSON shape: {"explanation": "<1-2 sentences>"}.'
)


def _fallback_explanation(decision: Decision, currency: str) -> str:
    method = decision.recommended_payment_method
    if method == "full_payment":
        return (
            f"Pay {currency} {decision.payments[0][1]} in full on {decision.payments[0][0]}. "
            f"This keeps the balance at or above the required minimum throughout the 90-day forecast."
        )
    if method == "partial_payment":
        first, second = decision.payments
        return (
            f"Pay {currency} {first[1]} on {first[0]}, then the remaining {currency} {second[1]} on {second[0]}. "
            f"Paying the full amount today is not safe, but this schedule completes it without breaking the minimum balance."
        )
    if method == "installments":
        n = len(decision.payments)
        return (
            f"Use the {n}-instalment plan starting {decision.payments[0][0]}, matching the supplied payment option. "
            f"This is the safest way to complete the request without breaking the minimum balance."
        )
    if method == "wait":
        return (
            f"Wait until {decision.payments[0][0]} to pay the full amount. "
            f"Paying today would break the required minimum balance within the 90-day forecast."
        )
    return "The full request cannot be completed safely within the 90-day forecast given current commitments and preferences."


def build_explanation(
    client: GroqClient,
    decision: Decision,
    request: dict,
    currency: str,
    spending_change_labels: list[str],
) -> str:
    fallback = _fallback_explanation(decision, currency)
    if client.api_key is None:
        return fallback

    plan_str = "; ".join(f"{d} pay {currency} {amt}" for d, amt in decision.payments) or "no payment"
    changes_str = ", ".join(spending_change_labels) or "none"
    user_content = (
        f"request_type: {request.get('request_type')}\n"
        f"requested_amount: {currency} {request.get('requested_amount')}\n"
        f"desired_completion_date: {request.get('desired_completion_date')}\n"
        f"affordability_status: {decision.affordability_status}\n"
        f"recommended_payment_method: {decision.recommended_payment_method}\n"
        f"amount_safe_to_pay: {currency} {decision.amount_safe_to_pay}\n"
        f"earliest_date_for_full_payment: {decision.earliest_date_for_full_payment or 'not within 90 days'}\n"
        f"payment_plan: {plan_str}\n"
        f"spending_changes_needed: {changes_str}\n"
    )
    result = client.extract_json(SYSTEM_PROMPT, user_content, purpose="explain_decision", max_tokens=150)
    if not result or not result.get("explanation"):
        return fallback
    return str(result["explanation"]).strip()
