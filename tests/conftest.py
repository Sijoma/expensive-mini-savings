"""Shared helpers: load real cases from tests/cases/, build small scenarios."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ems.core.model import Battery, Slot, Tariff

CASES = Path(__file__).parent / "cases"
"""Real cases from the author's house (private, not part of the public repository)."""
EXAMPLE = Path(__file__).parent / "example"
"""Made-up example data (tests/example/generate.py)."""
START = datetime.fromisoformat("2026-11-08T00:00:00+01:00")


@dataclass(frozen=True)
class Case:
    name: str
    note: str
    slots: list[Slot]
    soc_percent: float
    old_plan_actions: list[dict]
    old_plan_soc_percent: list[int]


def load_case(name: str) -> Case:
    file_path = CASES / f"{name}.json"
    if not file_path.exists():
        pytest.skip(f"private case {name} not available")
    raw = json.loads(file_path.read_text())
    slots = [
        Slot(datetime.fromisoformat(s["start"]), s["preis_ct"], s["pv_kw"], s["last_kw"])
        for s in raw["slots"]
    ]
    return Case(
        raw["name"],
        raw["notiz"],
        slots,
        raw["soc_prozent"],
        raw["alter_plan_aktionen"],
        raw["alter_plan_soc_prozent"],
    )


def scenario(*sections: tuple) -> list[Slot]:
    """Baut Viertelstunden aus (Anzahl, Preis ct, PV kW, Last kW[, Spitze kW])."""
    slots = []
    t = START
    for count, price, pv, load, *spike in sections:
        for _ in range(count):
            slots.append(Slot(t, price, pv, load, *spike))
            t += timedelta(minutes=15)
    return slots


@pytest.fixture
def battery() -> Battery:
    return Battery()


@pytest.fixture
def tariff() -> Tariff:
    return Tariff()


# `uv run pytest --hypothesis-profile=thorough` for many examples (before a release)
from hypothesis import settings  # noqa: E402

settings.register_profile("thorough", max_examples=2000, deadline=None)
