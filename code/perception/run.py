"""Orchestrates perception over the whole dataset once: every message row and
every blank-amount image gets a cached, structured signal. Called once from
main.py before the truth layer runs."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ingest.loader import Dataset
from perception.cache import JsonCache
from perception.groq_client import GroqClient
from perception.image_extractor import extract_amount_from_image
from perception.schema import ImageAmountSignal, MessageSignal
from perception.text_extractor import extract_message_signal


def run_perception(
    ds: Dataset, client: GroqClient, image_dir: Path, cache_dir: Path
) -> tuple[dict[str, MessageSignal], dict[str, ImageAmountSignal]]:
    message_cache = JsonCache(cache_dir / "message_signals.json")
    image_cache = JsonCache(cache_dir / "image_signals.json")

    message_signals: dict[str, MessageSignal] = {}
    for m in ds.messages:
        mid = m["message_id"]
        cached = message_cache.get(mid)
        if cached is not None:
            message_signals[mid] = MessageSignal(**cached)
            continue
        signal = extract_message_signal(client, m)
        message_signals[mid] = signal
        message_cache.set(mid, asdict(signal))
    message_cache.save()

    events_needing_image = {
        e["event_id"]: e for e in ds.events if e.get("amount", "") == ""
    }
    image_signals: dict[str, ImageAmountSignal] = {}
    for img in ds.images:
        related = img.get("related_event_id")
        if not related or related not in events_needing_image:
            continue
        img_id = img["image_id"]
        cached = image_cache.get(img_id)
        if cached is not None:
            image_signals[related] = ImageAmountSignal(**cached)
            continue
        signal = extract_amount_from_image(
            client, img_id, related, image_dir, events_needing_image[related]
        )
        image_signals[related] = signal
        image_cache.set(img_id, asdict(signal))
    image_cache.save()

    return message_signals, image_signals
