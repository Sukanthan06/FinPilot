"""Tiny local API + static file server for the ops dashboard in frontend/.
Stdlib only (http.server) -- no extra dependency just to serve a few JSON
endpoints and some static files locally. Run: python code/api_server.py
Then open http://localhost:8765/
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common.env import load_env
from common.money import fmt
from ingest.bundle import build_bundles
from ingest.currency import RateTable
from ingest.loader import load_dataset
from perception.groq_client import GroqClient
from perception.run import run_perception
from truth.resolve import build_financial_facts
from decide.engine import decide
from explain.explainer import build_explanation

ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "dataset"
IMAGE_DIR = DATASET_DIR / "media" / "images"
CACHE_DIR = ROOT / "code" / ".cache"
FRONTEND_DIR = ROOT / "frontend"
PORT = 8765

print("Loading dataset and running perception (cached)...")
load_env(ROOT / "code" / ".env")
DS = load_dataset(DATASET_DIR)
BUNDLES = build_bundles(DS)
RATES = RateTable(DS.exchange_rates)
CLIENT = GroqClient()
MESSAGE_SIGNALS, IMAGE_SIGNALS = run_perception(DS, CLIENT, IMAGE_DIR, CACHE_DIR)
print(f"Ready: {len(DS.requests)} requests loaded.")


def decision_payload(request_id: str) -> dict | None:
    bundle = BUNDLES.get(request_id)
    if bundle is None:
        return None
    facts = build_financial_facts(bundle, MESSAGE_SIGNALS, IMAGE_SIGNALS, RATES)
    d = decide(facts, bundle.request, bundle.payment_options)
    change_labels = [
        (f"stop:{c.reference_event_id}" if c.kind == "stop" else f"reduce_to:{c.reference_event_id}:{fmt(c.new_amount)}")
        for c in d.spending_changes
    ]
    explanation = build_explanation(CLIENT, d, bundle.request, facts.home_currency, change_labels)

    forecast_series = [
        {"date": dt.isoformat(), "balance": float(bal)}
        for dt, bal in zip(d.base_forecast.dates, d.base_forecast.base_balance)
    ]

    return {
        "request": bundle.request,
        "profile": {
            "home_currency": facts.home_currency,
            "current_available_balance": float(facts.starting_balance),
            "minimum_balance_to_keep": float(facts.minimum_balance),
            "payment_methods_user_will_consider": sorted(facts.payment_methods_accepted),
            "financial_priorities": facts.financial_priorities,
        },
        "decision": {
            "amount_safe_to_pay": fmt(d.amount_safe_to_pay),
            "affordability_status": d.affordability_status,
            "recommended_payment_method": d.recommended_payment_method,
            "payment_plan": "|".join(f"{dt.isoformat()}:{fmt(a)}" for dt, a in d.payments) or "none",
            "earliest_date_for_full_payment": d.earliest_date_for_full_payment.isoformat() if d.earliest_date_for_full_payment else "",
            "spending_changes_needed": "|".join(change_labels) or "none",
            "decision_explanation": explanation,
        },
        "forecast": forecast_series,
        "notes": facts.notes,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt_, *args):
        pass  # keep stdout clean

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/requests":
            items = [
                {"request_id": r["request_id"], "user_id": r["user_id"], "request_type": r["request_type"],
                 "requested_amount": r["requested_amount"], "request_date": r["request_date"]}
                for r in DS.requests
            ]
            self._send_json(items)
            return

        if path.startswith("/api/decision/"):
            rid = path.rsplit("/", 1)[-1]
            payload = decision_payload(rid)
            if payload is None:
                self._send_json({"error": f"unknown request_id {rid}"}, status=404)
            else:
                self._send_json(payload)
            return

        # static file serving
        rel = path.lstrip("/") or "index.html"
        file_path = (FRONTEND_DIR / rel).resolve()
        if FRONTEND_DIR not in file_path.parents and file_path != FRONTEND_DIR:
            self._send_json({"error": "forbidden"}, status=403)
            return
        if not file_path.exists():
            file_path = FRONTEND_DIR / "index.html"
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(file_path.suffix, "application/octet-stream")
        self._send_file(file_path, content_type)


def main():
    server = ThreadingHTTPServer(("localhost", PORT), Handler)
    print(f"Serving on http://localhost:{PORT}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
