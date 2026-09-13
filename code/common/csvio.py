"""Thin CSV helpers shared by every layer. No pandas — the dataset is small
enough that stdlib csv + dict rows is simpler and avoids a broken native
build toolchain on this machine (see NOTES.md)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: Iterable[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
