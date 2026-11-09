"""Simulation against reality: replay real days (backtest/data) with the real actions.

Checks the battery model (efficiencies, standby, spikes) against measured SoC curves. Quarter hours in
which the inverter did something HA does not show (app schedule, VPP) are skipped: there the simulated
SoC is set to the measured one. Skipped if no private measurement data is present.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise

import pytest

from backtest.measurement import all_days, load_day
from ems.core.model import DT_H, Action, Battery, Tariff
from ems.core.simulation import step

DAYS = all_days()


RESERVE_UNTIL_11_02 = 0.05
"""Backup reserve of the recorded days: 5 % until 2026-11-02, 0 % afterwards."""


def replay(day: str, battery: Battery) -> tuple[list[float], list[float]]:
    """Returns (simulated SoC, measured SoC) per quarter hour, in %."""
    measurements = load_day(day)
    if day < "2026-11-02" and battery.soc_min == 0:
        battery = replace(battery, soc_min=RESERVE_UNTIL_11_02)
    e = measurements[0].soc_percent / 100 * battery.capacity_kwh
    sim, real = [], []
    for m, following in pairwise(measurements):
        if following.soc_percent is None:
            continue
        if m.foreign:
            e = following.soc_percent / 100 * battery.capacity_kwh
            continue
        a = battery
        if m.ems_action is Action.GRID_CHARGE:  # measured charge power instead of the configured one
            a = replace(battery, grid_charge_kw=m.battery_charged_kwh / battery.efficiency_grid_charge / DT_H)
        e = step(e, m.as_slot(), m.ems_action, a, Tariff()).e_after
        sim.append(e / battery.capacity_kwh * 100)
        real.append(following.soc_percent)
    return sim, real


@pytest.mark.skipif(not DAYS, reason="keine Messdaten")
@pytest.mark.parametrize("day", DAYS)
def test_soc_follows_reality(day):
    sim, real = replay(day, Battery())
    deviation = [s - e for s, e in zip(sim, real, strict=True)]
    assert max(map(abs, deviation)) < 6.0, f"max. {max(map(abs, deviation)):.1f} %-Punkte"
    assert abs(sum(deviation) / len(deviation)) < 2.0


@pytest.mark.skipif(not DAYS, reason="keine Messdaten")
def test_measured_model_beats_old_assumption():
    """The measured efficiencies and standby fit better than the old 85 % round trip."""
    from ems.core.model import BATTERY_OLD_PLANNER

    def error(battery):
        total_sum = 0.0
        for day in DAYS:
            sim, real = replay(day, battery)
            total_sum += sum(abs(s - e) for s, e in zip(sim, real, strict=True)) / len(sim)
        return total_sum / len(DAYS)

    assert error(Battery()) < 0.6 * error(BATTERY_OLD_PLANNER)
