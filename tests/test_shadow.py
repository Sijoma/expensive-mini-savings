"""Savings ledger (shadow battery): measured against the same battery without control, and the promise in
the same terms. "Reality" in these tests is the model itself, plus deliberate deviations."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import START, scenario
from ems import shadow
from ems.core.explanation import create_plan
from ems.core.forecast import PV_EFFICIENCY
from ems.core.model import DT_H, Action, Battery, Slot, Tariff
from ems.core.simulation import simulate, step

BATTERY = Battery()
TARIFF = Tariff()
N, H, L = Action.NORMAL, Action.HOLD, Action.GRID_CHARGE


def measured(slot: Slot, action: Action, e: float, *, extra_import_kwh: float = 0.0) -> tuple[dict, float]:
    """One quarter hour as the measurement would record it if the house behaved exactly like the model
    (plus extra grid import the model does not know). Returns the record and the content afterwards."""
    s = step(e, slot, action, BATTERY, TARIFF)
    demand = (slot.load_kw - slot.pv_kw) * DT_H
    record = {
        "start": slot.start.isoformat(),
        "preis_ct": slot.price_ct,
        "pv_kw": slot.pv_kw / PV_EFFICIENCY,
        "last_kw": slot.load_kw,
        "bezug_brutto_kwh": max(demand, 0.0),
        "ueberschuss_brutto_kwh": max(-demand, 0.0),
        "akku_entladen_kwh": s.discharged_kwh,
        "akku_geladen_kwh": max(s.e_after - s.e_before + s.discharged_kwh, 0.0),
        "netz_bezug_kwh": s.import_kwh + extra_import_kwh,
        "netz_einspeisung_kwh": s.feed_in_kwh,
        "soc_prozent": e / BATTERY.capacity_kwh * 100,
        "aktion": action.value,
        "abgedeckt_s": 900,
    }
    return record, s.e_after


def run(slots: list[Slot], actions: list[Action], e0: float, **kwargs) -> tuple[dict, float]:
    ledger = shadow.new_ledger("2026-11-08")
    e = e0
    for slot, action in zip(slots, actions, strict=True):
        record, e = measured(slot, action, e, **kwargs)
        shadow.advance(ledger, record, BATTERY, TARIFF)
    return ledger, e


def test_without_control_nothing_is_saved():
    """Normal operation only: the shadow is the real battery, whatever the day looks like."""
    slots = scenario((20, 30, 0, 0.5), (30, 28, 4.0, 0.6), (30, 45, 0, 1.2))
    ledger, _ = run(slots, [N] * len(slots), 3.0)
    assert ledger["cash_ct"] == pytest.approx(0, abs=1e-9)
    assert ledger["diff_kwh"] == pytest.approx(0, abs=1e-9)


def test_night_charge_matches_the_simulated_savings():
    """Charge cheaply, use it in the expensive hours until both batteries are empty again: the ledger
    arrives at exactly the cost difference of the two simulations."""
    slots = scenario((8, 20, 0, 0.4), (90, 60, 0, 0.6))
    actions = [L] * 8 + [N] * 90
    ledger, e_end = run(slots, actions, 0.0)
    saved = (
        simulate(slots, 0.0, None, BATTERY, TARIFF).cost_ct
        - simulate(slots, 0.0, actions, BATTERY, TARIFF).cost_ct
    )
    assert e_end == pytest.approx(0, abs=0.05)
    assert saved > 100
    assert ledger["cash_ct"] == pytest.approx(saved, abs=0.01)
    assert ledger["diff_kwh"] == pytest.approx(0, abs=0.05)


def test_charged_energy_is_a_position_until_it_is_used():
    """Right after the charge the money is spent and the energy sits in the battery: cash is negative,
    the difference in content says why."""
    slots = scenario((8, 20, 0, 0.4), (4, 60, 0, 0.6))
    ledger, e = run(slots, [L] * 8 + [N] * 4, 0.0)
    assert ledger["cash_ct"] < -150
    assert ledger["diff_kwh"] == pytest.approx(e, abs=0.05)  # the shadow is still empty


def test_what_the_meter_sees_during_a_hold_counts():
    """The inverter draws 100 W from the grid while holding, which the model does not know: the ledger
    charges it, quarter hour by quarter hour."""
    slots = scenario((8, 30, 0, 0.5), (8, 50, 0, 0.5))
    actions = [H] * 8 + [N] * 8
    plain, _ = run(slots, actions, 2.0)
    ledger, _ = run(slots[:8], actions[:8], 2.0, extra_import_kwh=0.025)
    for slot, action in zip(slots[8:], actions[8:], strict=True):  # the rest as the model has it
        record, _ = measured(slot, action, 2.0 if slot is slots[8] else 0.0)
        record["soc_prozent"] = None  # only the hold matters here
        shadow.advance(ledger, record, BATTERY, TARIFF)
    held, _ = run(slots[:8], actions[:8], 2.0)
    assert ledger["cash_ct"] == pytest.approx(held["cash_ct"] - 8 * 0.025 * 30, abs=1e-6)
    assert plain["cash_ct"] > held["cash_ct"]  # the held energy pays off later, at 50 ct


def test_gaps_book_nothing_and_keep_the_difference():
    slots = scenario((8, 20, 0, 0.4), (6, 60, 0, 0.6))
    ledger, e = run(slots[:8], [L] * 8, 0.0)
    record, e = measured(slots[8], N, e)
    shadow.advance(ledger, record, BATTERY, TARIFF)
    before = (ledger["cash_ct"], ledger["diff_kwh"])
    for change in ({"soc_prozent": None}, {"abgedeckt_s": 300}, {"aktion": "x"}):
        record, _ = measured(slots[9], N, e)
        shadow.advance(ledger, {**record, **change}, BATTERY, TARIFF)
        assert (ledger["cash_ct"], ledger["diff_kwh"]) == before
    # a quarter hour without a price uses the last known one
    record, _ = measured(slots[10], N, e)
    shadow.advance(ledger, {**record, "preis_ct": None}, BATTERY, TARIFF)
    assert ledger["cash_ct"] > before[0]


def test_after_a_controlled_quarter_hour_the_next_soc_decides():
    """How much a grid charge really put into the battery is read from the SoC of the next quarter hour;
    if that one is missing (restart), the battery's own energy counter is the estimate."""
    slot, following = scenario((2, 20, 0, 0.4))
    record, e = measured(slot, L, 0.0)
    ledger = shadow.new_ledger("2026-11-08")
    shadow.advance(ledger, record, BATTERY, TARIFF)
    assert ledger["diff_kwh"] == pytest.approx(e, abs=0.01)  # estimate from the energy counter
    record, _ = measured(following, N, e - 0.3)  # the SoC says: 0.3 kWh less arrived
    shadow.advance(ledger, {**record, "soc_prozent": None}, BATTERY, TARIFF)
    assert ledger["diff_kwh"] == pytest.approx(e, abs=0.01)  # no SoC: the estimate stays

    ledger = shadow.new_ledger("2026-11-08")
    record, e = measured(slot, L, 0.0)
    shadow.advance(ledger, record, BATTERY, TARIFF)
    record, _ = measured(following, N, e - 0.3)
    shadow.advance(ledger, record, BATTERY, TARIFF)
    used = step(e - 0.3, following, N, BATTERY, TARIFF)
    assert ledger["diff_kwh"] == pytest.approx(used.e_after, abs=0.01)


def test_days_are_closed_with_their_change():
    ledger = shadow.new_ledger("2026-11-08")
    shadow.roll_day(ledger, "2026-11-08")
    ledger["cash_ct"] = 120.0
    shadow.roll_day(ledger, "2026-11-08")
    assert ledger["days"] == []
    ledger["position_ct"] = 30.0
    shadow.roll_day(ledger, "2026-11-09")
    ledger["cash_ct"] = 100.0
    shadow.roll_day(ledger, "2026-11-11")  # a day without measurements in between
    assert ledger["days"] == [{"tag": "2026-11-08", "wert": 1.5}, {"tag": "2026-11-09", "wert": -0.2}]
    assert shadow.total_ct(ledger) == 130.0


def test_position_is_what_the_planner_gains_from_the_extra_content():
    slots = scenario((40, 60, 0, 0.6))
    plan = create_plan(slots, 5.0, BATTERY, TARIFF)
    assert shadow.position_ct(slots, 5.0, 0.0, plan, BATTERY, TARIFF) == 0
    worth = shadow.position_ct(slots, 5.0, 5.0, plan, BATTERY, TARIFF)  # the shadow is empty
    # 5 kWh replace grid power at 60 ct, after discharge losses, standby and wear
    assert 5 * 35 < worth < 5 * 50
    half = shadow.position_ct(slots, 5.0, 2.5, plan, BATTERY, TARIFF)
    assert 0 < half < worth
    # the shadow cannot hold less than nothing: a larger difference is worth the same
    assert shadow.position_ct(slots, 5.0, 8.0, plan, BATTERY, TARIFF) == pytest.approx(worth)


def day_slots(*sections: tuple) -> list[Slot]:
    """Like scenario(), but starting at 14:00 the day before, as a plan made in the afternoon does."""
    slots = scenario((40, 40, 0, 0.5), *sections)
    return [Slot(s.start - timedelta(hours=10), s.price_ct, s.pv_kw, s.load_kw, s.spike_kw) for s in slots]


def test_promise_for_a_day_with_a_night_charge_is_not_a_loss():
    """The old forecast cut the plan after 24 h and valued the charged energy with the cheapest price, so
    a night charge for the next evening looked like a loss. Per calendar day, valued with the plan's own
    value of the content, it is the gain the plan makes."""
    tomorrow = START.date()  # day_slots() starts at 14:00 the day before START
    slots = day_slots((16, 20, 0, 0.4), (80, 60, 0, 0.6))  # 00:00–04:00 cheap, then expensive all day
    plan = create_plan(slots, 0.0, BATTERY, TARIFF)
    assert Action.GRID_CHARGE in plan.actions
    promised = shadow.promise_ct(plan, 0.0, BATTERY, TARIFF, tomorrow)
    assert promised is not None and promised > 100
    # both batteries are empty at midnight before and after: the promise is the plan's savings
    assert promised == pytest.approx(plan.without.cost_ct - plan.with_plan.cost_ct, abs=1.0)


def test_promise_needs_the_whole_day():
    slots = day_slots((16, 20, 0, 0.4), (40, 60, 0, 0.6))  # prices end at 14:00
    plan = create_plan(slots, 0.0, BATTERY, TARIFF)
    assert shadow.promise_ct(plan, 0.0, BATTERY, TARIFF, START.date()) is None


def test_promise_starts_from_the_shadow_not_from_the_real_battery():
    """Yesterday's control left 4 kWh in the battery. Using them tomorrow is not tomorrow's gain: the
    promise only counts what tomorrow's quarter hours add."""
    tomorrow = START.date()  # day_slots() starts at 14:00 the day before START
    slots = day_slots((96, 50, 0, 0.5))
    slots = [
        Slot(s.start, 20.0 if i < 40 else s.price_ct, 0, 0.0 if i < 40 else 0.5) for i, s in enumerate(slots)
    ]
    plan = create_plan(slots, 4.0, BATTERY, TARIFF)  # nothing to do: no load today, flat price tomorrow
    same = shadow.promise_ct(plan, 4.0, BATTERY, TARIFF, tomorrow)
    carried = shadow.promise_ct(plan, 0.0, BATTERY, TARIFF, tomorrow)
    assert same == pytest.approx(0, abs=1)
    assert carried == pytest.approx(0, abs=25)  # the 4 kWh are worth ≈ 1.40 €, none of it is promised
