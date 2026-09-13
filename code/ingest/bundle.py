"""Builds one self-contained RequestBundle per request: the request row, the
user's profile, every event for that user (amount normalized to home
currency where possible), the request's payment options, and the messages /
images relevant to the user or the request. This is the only join point —
everything downstream (perception, truth, forecast, decide) reads from a
RequestBundle and never touches raw CSV rows again.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from common.money import to_decimal
from ingest.currency import RateTable
from ingest.loader import Dataset


@dataclass
class NormalizedEvent:
    raw: dict
    event_id: str
    user_id: str
    event_type: str
    category: str
    direction: str
    amount_home: Decimal | None  # None until perception resolves a blank amount
    amount_unresolved: bool
    currency: str
    event_date: str
    settlement_date: str
    status: str
    linked_event_id: str
    flexibility: str
    minimum_allowed_amount: Decimal | None


@dataclass
class RequestBundle:
    request: dict
    profile: dict
    events: list[NormalizedEvent]
    payment_options: list[dict]
    messages: list[dict]
    images: list[dict]


def normalize_events(events: list[dict], home_currency: str, rates: RateTable) -> list[NormalizedEvent]:
    out = []
    for e in events:
        raw_amount = to_decimal(e["amount"])
        unresolved = e["amount"] in (None, "")
        amount_home = None
        if raw_amount is not None:
            settle_date = e["settlement_date"] or e["event_date"]
            amount_home = rates.convert(raw_amount, settle_date, e["currency"], home_currency)
            if amount_home is None:
                # Same currency already handled inside RateTable.convert (rate=1);
                # a genuine None here means a foreign-currency pair with no rate
                # row at all -- flag as unresolved rather than invent a number.
                unresolved = True
        out.append(
            NormalizedEvent(
                raw=e,
                event_id=e["event_id"],
                user_id=e["user_id"],
                event_type=e["event_type"],
                category=e["category"],
                direction=e["direction"],
                amount_home=amount_home,
                amount_unresolved=unresolved,
                currency=e["currency"],
                event_date=e["event_date"],
                settlement_date=e["settlement_date"] or e["event_date"],
                status=e["status"],
                linked_event_id=e["linked_event_id"],
                flexibility=e["flexibility"],
                minimum_allowed_amount=to_decimal(e["minimum_allowed_amount"]),
            )
        )
    return out


def build_bundles(ds: Dataset) -> dict[str, RequestBundle]:
    rates = RateTable(ds.exchange_rates)
    normalized_cache: dict[str, list[NormalizedEvent]] = {}
    bundles: dict[str, RequestBundle] = {}

    for req in ds.requests:
        user_id = req["user_id"]
        profile = ds.profiles_by_user[user_id]
        home_currency = profile["home_currency"]

        if user_id not in normalized_cache:
            normalized_cache[user_id] = normalize_events(
                ds.events_by_user.get(user_id, []), home_currency, rates
            )

        request_id = req["request_id"]
        messages = list(ds.messages_by_request.get(request_id, []))
        # user-level messages not tied to a specific request (e.g. payroll
        # notices) are still relevant context for that user's forecast
        user_messages = [
            m for m in ds.messages_by_user.get(user_id, []) if not m.get("request_id")
        ]

        bundles[request_id] = RequestBundle(
            request=req,
            profile=profile,
            events=normalized_cache[user_id],
            payment_options=ds.options_by_request.get(request_id, []),
            messages=messages + user_messages,
            images=ds.images_by_request.get(request_id, [])
            + [img for img in ds.images_by_user.get(user_id, []) if not img.get("request_id")],
        )
    return bundles
