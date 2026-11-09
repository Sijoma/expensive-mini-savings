"""What the optimizer must be able to do (written before the optimizer)."""

from __future__ import annotations

from itertools import pairwise, product

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import load_case, scenario
from ems.core.forecast import with_spikes
from ems.core.model import Action, Battery, Tariff, executable
from ems.core.optimizer import plan_actions
from ems.core.simulation import simulate

TOLERANCE_CT = 1.0
"""The optimizer works on a SoC grid, so it may be marginally worse than exact."""

SWITCH_TOLERANCE_CT = 2.0
"""Per action it may be worse by up to the switch fee (it deliberately skips tiny gains)."""


def fees(actions):
    codes = "n" + "".join(actions)
    return SWITCH_TOLERANCE_CT * sum(1 for a, b in pairwise(codes) if b != "n" and a != b)


def valuation(slots, e0, actions, battery=None, tariff=None):
    return simulate(slots, e0, actions, battery or Battery(), tariff or Tariff()).valuation_ct


# --- minimum duration rules ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("codes", "ok"),
    [
        ("nnnn", True),
        ("nhhn", True),
        ("nhnn", False),
        ("hnnn", False),
        ("nnnh", True),  # a block may be short at the end
        ("lnhhhl", True),
    ],
)
def test_executable(codes, ok):
    assert executable([Action(c) for c in codes]) is ok


# --- kleine, durchschaubare Szenarien ----------------------------------------------------------


def test_holds_morning_for_expensive_evening(battery, tariff):
    """PV does not fill the battery during the day, so keeping energy for the evening pays."""
    slots = scenario(
        (8, 40, 0.0, 1.0),  # Morgen: 2 kWh Bedarf zu 40 ct
        (16, 45, 1.5, 1.0),  # day: 2 kWh surplus, not enough for the evening
        (16, 66, 0.0, 1.0),  # Abend: 4 kWh Bedarf zu 66 ct
    )
    actions = plan_actions(slots, 3.0, battery, tariff)
    assert Action.HOLD in actions[:8]
    assert valuation(slots, 3.0, actions) < valuation(slots, 3.0, None) - 30


def test_no_hold_when_pv_fills_battery_anyway(battery, tariff):
    slots = scenario(
        (8, 40, 0.0, 1.0),
        (16, 45, 7.0, 1.0),  # 24 kWh surplus: the battery gets full anyway
        (16, 66, 0.0, 1.0),
    )
    actions = plan_actions(slots, 3.0, battery, tariff)
    assert Action.HOLD not in actions[:8]


def test_no_grid_charge_when_spread_too_small(battery, tariff):
    # charging at 40 ct → (40 + wear) / efficiency ≈ 58 ct > 50 ct later
    slots = scenario((8, 40, 0.0, 0.4), (8, 50, 0.0, 1.0))
    actions = plan_actions(slots, battery.e_min, battery, tariff)
    assert Action.GRID_CHARGE not in actions


def test_grid_charge_when_spread_large(battery, tariff):
    slots = scenario((8, 20, 0.0, 0.4), (8, 70, 0.0, 1.0))
    actions = plan_actions(slots, battery.e_min, battery, tariff)
    assert Action.GRID_CHARGE in actions[:8]
    assert Action.GRID_CHARGE not in actions[8:]


def test_no_price_spread_no_action(battery, tariff):
    slots = scenario((16, 40, 0.0, 1.0), (16, 40, 3.0, 1.0), (16, 40, 0.0, 1.0))
    assert set(plan_actions(slots, 5.0, battery, tariff)) == {Action.NORMAL}


def test_no_toggling_at_nearly_equal_prices(battery, tariff):
    """In the morning prices alternate by a tenth of a cent and the battery is short for the evening:
    hold in one block, not alternating."""
    tomorrow = [(1, 47.7 + 0.1 * (i % 2), 0.0, 1.0) for i in range(16)]
    slots = scenario(*tomorrow, (16, 66, 0.0, 1.0))
    actions = plan_actions(slots, 4.0, battery, tariff)
    codes = "".join(actions[:16])
    assert codes.count("nh") + codes.startswith("h") <= 1, codes


def test_running_hold_may_end_at_once(battery, tariff):
    slots = scenario((4, 66, 0.0, 1.0))  # expensive now: use the battery
    assert plan_actions(slots, 5.0, battery, tariff, hold_running=True)[0] is Action.NORMAL


def test_running_hold_continues_without_fee(battery, tariff):
    # 55 ct later: holding pays (40 < 55), grid charging does not ((40 + 9) / 0.85 ≈ 58 > 55)
    slots = scenario((1, 40, 0.0, 1.0), (8, 55, 0.0, 1.0))
    assert plan_actions(slots, 1.5, battery, tariff, hold_running=True)[0] is Action.HOLD


def test_young_hold_must_continue(battery, tariff):
    slots = scenario((4, 66, 0.0, 1.0))  # eigentlich lohnt Entladen
    plan = plan_actions(slots, 5.0, battery, tariff, running=Action.HOLD, since=1)
    assert plan[0] is Action.HOLD
    assert plan[1] is Action.NORMAL


# --- properties for arbitrary inputs ----------------------------------------------------------

slot_st = st.tuples(
    st.floats(0, 80), st.floats(0, 8), st.floats(0, 5), st.one_of(st.just(0.0), st.floats(0, 2))
)


@settings(deadline=None)
@given(st.lists(slot_st, min_size=1, max_size=48), st.floats(0.05, 1.0))
def test_never_worse_than_no_control(raw, soc):
    battery, tariff = Battery(), Tariff()
    slots = scenario(*[(1, *r) for r in raw])
    e0 = soc * battery.capacity_kwh
    actions = plan_actions(slots, e0, battery, tariff)
    assert len(actions) == len(slots)
    assert executable(actions)
    assert valuation(slots, e0, actions) + fees(actions) <= valuation(slots, e0, None) + TOLERANCE_CT


@settings(deadline=None)
@given(st.lists(slot_st, min_size=1, max_size=6), st.floats(0.05, 1.0))
def test_as_good_as_brute_force(raw, soc):
    """For short horizons enumerate all 3^n plans: the optimizer must find the best one."""
    battery, tariff = Battery(), Tariff()
    slots = scenario(*[(1, *r) for r in raw])
    e0 = soc * battery.capacity_kwh
    best_value = min(
        valuation(slots, e0, list(p)) + fees(p)
        for p in product(list(Action), repeat=len(slots))
        if executable(list(p))
    )
    plan = plan_actions(slots, e0, battery, tariff)
    assert valuation(slots, e0, plan) + fees(plan) <= best_value + TOLERANCE_CT


# --- holding during the day against spikes and forecast errors -------------------------------


def day_with_spikes(evening_ct, pv_kw=1.5):
    """Morning without PV, day with a slight surplus and spikes, evening without PV. Starts with 0.7 kWh."""
    return with_spikes(
        scenario((4, 45, 0.0, 1.0), (24, 46, pv_kw, 1.0), (16, evening_ct, 0.0, 1.0)),
    )


def test_daytime_hold_when_evening_more_expensive(battery, tariff):
    """Hold until the battery covers the evening. Keeping more would only add residual value."""
    slots = day_with_spikes(66)
    actions = plan_actions(slots, 0.7, battery, tariff)
    day = "".join(actions[4:28])
    assert day.count("h") >= 12, day
    evening = simulate(slots, 0.7, actions, battery, tariff).steps[28:]
    assert sum(s.import_kwh for s in evening) < 0.1


def test_no_daytime_hold_when_evening_cheaper(battery, tariff):
    actions = plan_actions(day_with_spikes(40), 0.7, battery, tariff)
    assert Action.HOLD not in actions[4:28]


def test_no_daytime_hold_when_battery_fills_anyway(battery, tariff):
    actions = plan_actions(day_with_spikes(66, pv_kw=6.0), 0.7, battery, tariff)
    day = "".join(actions[4:28])
    assert day.count("h") <= 4, day


# --- real cases (private data, skipped if missing) -------------------------------------------


def test_case_hold_in_the_morning(battery, tariff):
    """At 05:00 the battery should have been held for the evening (66 ct)."""
    case = load_case("2026-11-08_0500")
    e0 = case.soc_percent / 100 * battery.capacity_kwh
    actions = plan_actions(case.slots, e0, battery, tariff)
    morning = [a for s, a in zip(case.slots, actions, strict=True) if s.start.hour < 9]
    assert Action.HOLD in morning
    assert valuation(case.slots, e0, actions) < valuation(case.slots, e0, None) - 10


def test_case_hold_during_the_day(battery, tariff):
    """At 10:00 the battery should have been held until the evening."""
    case = load_case("2026-11-08_1000")
    slots = with_spikes(case.slots)
    e0 = case.soc_percent / 100 * battery.capacity_kwh
    actions = plan_actions(slots, e0, battery, tariff)
    day = "".join(a for s, a in zip(slots, actions, strict=True) if 10 <= s.start.hour < 15)
    assert day.count("h") >= 0.8 * len(day), day


def test_runtime_full_horizon(battery, tariff):
    """A Pi 4 is about 3–4× slower than a laptop: stay below 0.3 s here."""
    import time

    case = load_case("2026-11-08_1315")
    t0 = time.perf_counter()
    plan_actions(case.slots, case.soc_percent / 100 * battery.capacity_kwh, battery, tariff)
    assert time.perf_counter() - t0 < 0.3
