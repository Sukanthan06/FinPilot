"""Loads every dataset/*.csv into plain lists of dicts and builds the id
indexes the rest of the pipeline joins on. No numeric/currency work happens
here — that is currency.py's job — this module only reads and indexes."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from common.csvio import read_csv


@dataclass
class Dataset:
    requests: list[dict]
    sample_requests: list[dict]
    profiles: list[dict]
    events: list[dict]
    exchange_rates: list[dict]
    payment_options: list[dict]
    messages: list[dict]
    images: list[dict]

    profiles_by_user: dict = field(default_factory=dict)
    events_by_user: dict = field(default_factory=dict)
    events_by_id: dict = field(default_factory=dict)
    options_by_request: dict = field(default_factory=dict)
    messages_by_user: dict = field(default_factory=dict)
    messages_by_request: dict = field(default_factory=dict)
    images_by_user: dict = field(default_factory=dict)
    images_by_request: dict = field(default_factory=dict)
    images_by_related_event: dict = field(default_factory=dict)

    def index(self) -> "Dataset":
        for p in self.profiles:
            self.profiles_by_user[p["user_id"]] = p
        for e in self.events:
            self.events_by_id[e["event_id"]] = e
            self.events_by_user.setdefault(e["user_id"], []).append(e)
        for o in self.payment_options:
            self.options_by_request.setdefault(o["request_id"], []).append(o)
        for m in self.messages:
            self.messages_by_user.setdefault(m["user_id"], []).append(m)
            if m.get("request_id"):
                self.messages_by_request.setdefault(m["request_id"], []).append(m)
        for img in self.images:
            self.images_by_user.setdefault(img["user_id"], []).append(img)
            if img.get("request_id"):
                self.images_by_request.setdefault(img["request_id"], []).append(img)
            if img.get("related_event_id"):
                self.images_by_related_event[img["related_event_id"]] = img
        return self


def load_dataset(dataset_dir: Path) -> Dataset:
    ds = Dataset(
        requests=read_csv(dataset_dir / "requests.csv"),
        sample_requests=read_csv(dataset_dir / "sample_requests.csv"),
        profiles=read_csv(dataset_dir / "financial_profiles.csv"),
        events=read_csv(dataset_dir / "financial_events.csv"),
        exchange_rates=read_csv(dataset_dir / "exchange_rates.csv"),
        payment_options=read_csv(dataset_dir / "request_payment_options.csv"),
        messages=read_csv(dataset_dir / "messages.csv"),
        images=read_csv(dataset_dir / "images.csv"),
    )
    return ds.index()
