"""Backtest with real days: replan every 15 min, execute against the measurements (private data)."""

from __future__ import annotations

from itertools import pairwise

import pytest

from backtest.measurement import all_days
from backtest.run import backtest, load
from ems.core.model import Battery, Tariff

DAYS = all_days()


@pytest.mark.skipif(len(DAYS) < 2, reason="zu wenig Messdaten")
def test_planner_saves_with_realistic_forecast():
    """Two days with the load forecast from the profile (as live): must save and must not toggle."""
    measurements = load(["2026-11-07", "2026-11-08"])
    battery, tariff = Battery(), Tariff()
    without = backtest(measurements, "none", battery, tariff)
    profile = backtest(measurements, "profile", battery, tariff)
    saved = without.valuation_ct - profile.valuation_ct
    assert saved > 50, f"only {saved:.0f} ct"
    a = "n" + profile.actions()
    switches = sum(1 for x, y in pairwise(a) if y != "n" and x != y)
    assert switches <= 6


@pytest.mark.skipif(len(DAYS) < 2, reason="zu wenig Messdaten")
def test_saves_at_least_as_much_as_manual_hold():
    """On that day a manual hold saved ≈ 0.55 €. The planner must save at least as much."""
    measurements = load(["2026-11-07", "2026-11-08"])
    battery, tariff = Battery(), Tariff()
    without = backtest(measurements, "none", battery, tariff).per_day()["2026-11-08"]
    profile = backtest(measurements, "profile", battery, tariff).per_day()["2026-11-08"]
    assert without - profile >= 55
