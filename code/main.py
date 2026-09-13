"""Entry point: reads dataset/, runs the full pipeline, writes output.csv at
the repo root. Run: python code/main.py"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.env import load_env  # noqa: E402
from common.money import fmt  # noqa: E402
from ingest.bundle import build_bundles  # noqa: E402
from ingest.currency import RateTable  # noqa: E402
from ingest.loader import load_dataset  # noqa: E402
from perception.groq_client import GroqClient  # noqa: E402
from perception.run import run_perception  # noqa: E402
from truth.resolve import build_financial_facts  # noqa: E402
from decide.engine import decide  # noqa: E402
from decide.spending_changes import SpendingChangeOption  # noqa: E402
from explain.explainer import build_explanation  # noqa: E402
from audit.validate import audit_output  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "dataset"
IMAGE_DIR = DATASET_DIR / "media" / "images"
CACHE_DIR = ROOT / "code" / ".cache"
OUTPUT_PATH = ROOT / "output.csv"
USAGE_REPORT_PATH = ROOT / "evaluation" / "usage_report.md"

OUTPUT_COLUMNS = [
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
]


def format_plan(payments) -> str:
    if not payments:
        return "none"
    return "|".join(f"{d.isoformat()}:{fmt(amt)}" for d, amt in payments)


def format_changes(changes: list[SpendingChangeOption]) -> str:
    if not changes:
        return "none"
    labels = []
    for c in changes[:3]:
        if c.kind == "stop":
            labels.append(f"stop:{c.reference_event_id}")
        else:
            labels.append(f"reduce_to:{c.reference_event_id}:{fmt(c.new_amount)}")
    return "|".join(labels)


def main() -> None:
    t0 = time.time()
    load_env(ROOT / "code" / ".env")

    print("Loading dataset...")
    ds = load_dataset(DATASET_DIR)
    bundles = build_bundles(ds)
    rates = RateTable(ds.exchange_rates)

    print("Running perception (Groq text + vision extraction, cached)...")
    client = GroqClient()
    message_signals, image_signals = run_perception(ds, client, IMAGE_DIR, CACHE_DIR)

    print(f"Deciding {len(ds.requests)} requests...")
    rows = []
    flexible_event_ids: set[str] = set()
    payment_options_by_request: dict[str, list[dict]] = {}

    for i, req in enumerate(ds.requests, 1):
        rid = req["request_id"]
        if i % 10 == 0 or i == 1:
            print(f"  [{i}/{len(ds.requests)}] {rid}", flush=True)
        bundle = bundles[rid]
        facts = build_financial_facts(bundle, message_signals, image_signals, rates)
        decision = decide(facts, req, bundle.payment_options)

        for e in bundle.events:
            if e.flexibility in ("reducible", "stoppable", "reducible_or_stoppable"):
                flexible_event_ids.add(e.event_id)
        payment_options_by_request[rid] = bundle.payment_options

        change_labels = [
            (f"stop:{c.reference_event_id}" if c.kind == "stop" else f"reduce_to:{c.reference_event_id}:{fmt(c.new_amount)}")
            for c in decision.spending_changes
        ]
        explanation = build_explanation(client, decision, req, facts.home_currency, change_labels)

        rows.append(
            {
                "request_id": rid,
                "amount_safe_to_pay": fmt(decision.amount_safe_to_pay),
                "affordability_status": decision.affordability_status,
                "recommended_payment_method": decision.recommended_payment_method,
                "payment_plan": format_plan(decision.payments),
                "earliest_date_for_full_payment": decision.earliest_date_for_full_payment.isoformat()
                if decision.earliest_date_for_full_payment
                else "",
                "spending_changes_needed": format_changes(decision.spending_changes),
                "decision_explanation": explanation,
            }
        )

    print("Auditing output...")
    requests_by_id = {r["request_id"]: r for r in ds.requests}
    errors = audit_output(rows, requests_by_id, payment_options_by_request, flexible_event_ids)
    if errors:
        print(f"AUDIT FOUND {len(errors)} ISSUE(S):")
        for e in errors[:50]:
            print(" -", e)
    else:
        print("Audit passed: no invariant violations found.")

    from common.csvio import write_csv
    write_csv(OUTPUT_PATH, rows, OUTPUT_COLUMNS)
    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")

    write_usage_report(client, time.time() - t0, len(ds.requests))


# Groq on-demand pricing per 1M tokens (input, output), checked live against
# console.groq.com/docs/models on 2026-09-13 for the two models this pipeline
# actually calls.
PRICE_PER_MILLION = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "qwen/qwen3.8-27b": (0.80, 4.00),
}


def write_usage_report(client: GroqClient, elapsed_seconds: float, num_requests: int) -> None:
    from collections import defaultdict

    by_model = defaultdict(lambda: {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
    for c in client.usage.calls:
        m = by_model[c["model"]]
        m["calls"] += 1
        m["prompt_tokens"] += c["prompt_tokens"]
        m["completion_tokens"] += c["completion_tokens"]

    lines = [
        "# Token Usage and Cost Report",
        "",
        f"Provider: Groq (https://groq.com). Final full-dataset run: {num_requests} requests, "
        f"{elapsed_seconds:.1f}s wall time.",
        "",
        "| Model | Purpose | Calls | Prompt tokens | Completion tokens | Total tokens | Est. cost (USD) |",
        "|---|---|---|---|---|---|---|",
    ]
    grand_total_tokens = 0
    grand_total_calls = 0
    total_cost = 0.0
    purpose_by_model = {
        "openai/gpt-oss-120b": "message classification (perception) + decision_explanation (explain)",
        "qwen/qwen3.8-27b": "blank-amount image extraction (perception)",
    }
    for model, m in by_model.items():
        total_tokens = m["prompt_tokens"] + m["completion_tokens"]
        in_price, out_price = PRICE_PER_MILLION.get(model, (0.0, 0.0))
        cost = (m["prompt_tokens"] / 1_000_000) * in_price + (m["completion_tokens"] / 1_000_000) * out_price
        total_cost += cost
        grand_total_tokens += total_tokens
        grand_total_calls += m["calls"]
        lines.append(
            f"| {model} | {purpose_by_model.get(model, '')} | {m['calls']} | {m['prompt_tokens']} | "
            f"{m['completion_tokens']} | {total_tokens} | ${cost:.4f} |"
        )

    lines.append(f"| **Total** | | **{grand_total_calls}** | | | **{grand_total_tokens}** | **${total_cost:.4f}** |")
    lines.append("")
    lines.append(f"Average tokens per request: {grand_total_tokens / max(num_requests, 1):.1f}")
    lines.append(f"Average cost per request: ${total_cost / max(num_requests, 1):.6f}")
    lines.append("")
    lines.append(
        "Pricing source: console.groq.com/docs/models, read at build time for the exact model ids "
        "used above. Message/image extraction results are cached to code/.cache/ across runs, so "
        "re-running main.py without deleting that cache makes zero further Groq calls."
    )

    USAGE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
