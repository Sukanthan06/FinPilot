"""Scores the pipeline against dataset/sample_requests.csv, the 25 solved
examples the challenge provides for validation. sample_requests.csv uses a
disjoint set of request_ids/users from dataset/requests.csv (verified: zero
overlap), so this runs the exact same ingest/perception/truth/forecast/
decide/explain pipeline as main.py, just pointed at the sample requests
instead -- nothing from this file's ground-truth columns is ever fed back
into the decision logic. Run: python evaluation/eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "code"))

from common.env import load_env  # noqa: E402
from common.money import fmt  # noqa: E402
from ingest.bundle import build_bundles  # noqa: E402
from ingest.currency import RateTable  # noqa: E402
from ingest.loader import load_dataset  # noqa: E402
from perception.groq_client import GroqClient  # noqa: E402
from perception.run import run_perception  # noqa: E402
from truth.resolve import build_financial_facts  # noqa: E402
from decide.engine import decide  # noqa: E402
from explain.explainer import build_explanation  # noqa: E402
from main import format_changes, format_plan  # noqa: E402

DATASET_DIR = ROOT / "dataset"
IMAGE_DIR = DATASET_DIR / "media" / "images"
CACHE_DIR = ROOT / "code" / ".cache"

METRICS = [
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
]


def close_enough(a: str, b: str, tol=0.01) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except ValueError:
        return a == b


def main() -> None:
    load_env(ROOT / "code" / ".env")
    ds = load_dataset(DATASET_DIR)
    ds.requests = ds.sample_requests  # evaluate against the 25 solved examples instead
    bundles = build_bundles(ds)
    rates = RateTable(ds.exchange_rates)

    client = GroqClient()
    message_signals, image_signals = run_perception(ds, client, IMAGE_DIR, CACHE_DIR)

    scores = {m: 0 for m in METRICS}
    total = 0
    rows_out = []

    for req in ds.sample_requests:
        rid = req["request_id"]
        bundle = bundles[rid]
        facts = build_financial_facts(bundle, message_signals, image_signals, rates)
        decision = decide(facts, req, bundle.payment_options)
        change_labels = [
            (f"stop:{c.reference_event_id}" if c.kind == "stop" else f"reduce_to:{c.reference_event_id}:{fmt(c.new_amount)}")
            for c in decision.spending_changes
        ]
        explanation = build_explanation(client, decision, req, facts.home_currency, change_labels)

        predicted = {
            "amount_safe_to_pay": fmt(decision.amount_safe_to_pay),
            "affordability_status": decision.affordability_status,
            "recommended_payment_method": decision.recommended_payment_method,
            "payment_plan": format_plan(decision.payments),
            "earliest_date_for_full_payment": decision.earliest_date_for_full_payment.isoformat()
            if decision.earliest_date_for_full_payment else "",
            "spending_changes_needed": format_changes(decision.spending_changes),
        }

        total += 1
        row_report = {"request_id": rid}
        for m in METRICS:
            expected = req[m]
            got = predicted[m]
            ok = close_enough(got, expected) if m == "amount_safe_to_pay" else got == expected
            scores[m] += int(ok)
            row_report[m] = (ok, expected, got)
        rows_out.append((row_report, explanation))

    print(f"Scored {total} sample requests\n")
    for m in METRICS:
        pct = 100 * scores[m] / total
        print(f"  {m:32s} {scores[m]:3d}/{total}  ({pct:5.1f}%)")

    print("\nMismatches:")
    for row_report, _ in rows_out:
        rid = row_report["request_id"]
        mismatches = [m for m in METRICS if not row_report[m][0]]
        if mismatches:
            print(f"  {rid}:")
            for m in mismatches:
                ok, expected, got = row_report[m]
                print(f"    {m}: expected={expected!r} got={got!r}")


if __name__ == "__main__":
    main()
