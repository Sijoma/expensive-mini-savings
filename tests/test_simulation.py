"""Battery physics and cost. The basis for everything else, so it is checked strictly."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from conftest import load_case, scenario
from ems.core.model import BATTERY_OLD_PLANNER, DT_H, Action, Battery, Slot, Tariff
from ems.core.simulation import simulate, step, step_vector

EPS = 1e-9


# --- einzelne Viertelstunden -------------------------------------------------------------------


def test_normal_covers_demand_from_battery(battery, tariff):
    (slot,) = scenario((1, 50, 0.0, 2.0))  # 0,5 kWh Bedarf
    s = step(5.0, slot, Action.NORMAL, battery, tariff)
    sb = battery.standby_kw * DT_H  # at night the inverter runs from the battery
    assert s.import_kwh == pytest.approx(0.0)
    assert s.discharged_kwh == pytest.approx(0.5 / battery.efficiency_discharge + sb)
    assert s.standby_kwh == pytest.approx(sb)
    assert s.e_after == pytest.approx(5.0 - 0.5 / battery.efficiency_discharge - sb)


def test_no_standby_from_battery_during_day(battery, tariff):
    (slot,) = scenario((1, 50, 0.5, 2.5))  # PV running, 0.5 kWh demand
    s = step(5.0, slot, Action.NORMAL, battery, tariff)
    assert s.standby_kwh == 0
    assert s.discharged_kwh == pytest.approx(0.5 / battery.efficiency_discharge)


def test_held_no_standby(battery, tariff):
    """Measured: when the battery is held or empty, grid import equals consumption."""
    (slot,) = scenario((1, 50, 0.0, 2.0))
    s = step(5.0, slot, Action.HOLD, battery, tariff)
    assert s.standby_kwh == 0
    assert s.import_kwh == pytest.approx(0.5)


def test_small_night_load_is_expensive(battery):
    """At 0.4 kW only ~80 % of the content reaches the house, at 2 kW ~93 %."""
    from ems.core.simulation import usable_per_kwh

    assert usable_per_kwh(0.4, battery) == pytest.approx(0.785, abs=0.01)
    assert usable_per_kwh(2.0, battery) == pytest.approx(0.93, abs=0.01)


def test_normal_empty_imports_from_grid(battery, tariff):
    (slot,) = scenario((1, 50, 0.0, 2.0))
    s = step(battery.e_min, slot, Action.NORMAL, battery, tariff)
    assert s.import_kwh == pytest.approx(0.5)
    assert s.e_after == pytest.approx(battery.e_min)


def test_discharge_power_is_limited(tariff):
    battery = Battery(discharge_kw=2.0)
    (slot,) = scenario((1, 50, 0.0, 6.0))  # 1.5 kWh demand, the battery may deliver 0.5 kWh
    s = step(8.0, slot, Action.NORMAL, battery, tariff)
    assert s.import_kwh == pytest.approx(1.0)


def test_hold_keeps_the_content(battery, tariff):
    (slot,) = scenario((1, 50, 0.0, 2.0))
    s = step(5.0, slot, Action.HOLD, battery, tariff)
    assert s.e_after == 5.0
    assert s.import_kwh == pytest.approx(0.5)
    assert s.cost_ct == pytest.approx(25.0)


def test_hold_lets_pv_charge(battery, tariff):
    """Hold via the discharge limit: PV surplus still goes into the battery."""
    (slot,) = scenario((1, 50, 3.0, 1.0))  # 0.5 kWh surplus
    normal = step(5.0, slot, Action.NORMAL, battery, tariff)
    held = step(5.0, slot, Action.HOLD, battery, tariff)
    assert held == replace(normal, action=Action.HOLD)
    assert held.e_after == pytest.approx(5.0 + 0.5 * battery.efficiency_charge)
    assert held.feed_in_kwh == pytest.approx(0.0)


def test_full_battery_exports(battery):
    tariff = Tariff(feed_in_ct=8.0)
    (slot,) = scenario((1, 50, 5.0, 1.0))  # 1 kWh surplus
    s = step(battery.e_max, slot, Action.NORMAL, battery, tariff)
    assert s.feed_in_kwh == pytest.approx(1.0)
    assert s.cost_ct == pytest.approx(-8.0)


def test_grid_charge_at_full_power(battery, tariff):
    (slot,) = scenario((1, 30, 0.0, 1.0))
    s = step(3.0, slot, Action.GRID_CHARGE, battery, tariff)
    in_ac = battery.grid_charge_kw * DT_H
    assert s.e_after == pytest.approx(3.0 + in_ac * battery.efficiency_grid_charge)
    assert s.import_kwh == pytest.approx(0.25 + in_ac)
    assert s.grid_charged_kwh == pytest.approx(in_ac)


def test_grid_charge_stops_when_full(battery, tariff):
    (slot,) = scenario((1, 30, 0.0, 1.0))
    s = step(battery.e_max - 0.1, slot, Action.GRID_CHARGE, battery, tariff)
    assert s.e_after == pytest.approx(battery.e_max)
    assert s.grid_charged_kwh == pytest.approx(0.1 / battery.efficiency_grid_charge)


def test_grid_charge_uses_pv_first(battery, tariff):
    (slot,) = scenario((1, 30, 4.0, 1.0))  # 0.75 kWh surplus, charging 1.25 kWh
    s = step(3.0, slot, Action.GRID_CHARGE, battery, tariff)
    assert s.grid_charged_kwh == pytest.approx(0.5)
    assert s.import_kwh == pytest.approx(0.5)


def test_wear_counts_per_kwh_taken(tariff):
    battery = Battery(wear_ct=10.0)
    (slot,) = scenario((1, 0, 0.0, 2.0))  # energy is free, only wear counts
    s = step(5.0, slot, Action.NORMAL, battery, tariff)
    assert s.cost_ct == pytest.approx(s.discharged_kwh * 10.0)


# --- spikes within the quarter hour ----------------------------------------------------------


def test_spike_passes_battery_when_normal(battery, tariff):
    """1 kW mean surplus plus 1 kW spike: 0.5 kWh in, 0.25 kWh out, no grid."""
    (slot,) = scenario((1, 50, 3.0, 2.0, 1.0))
    s = step(5.0, slot, Action.NORMAL, battery, tariff)
    assert s.import_kwh == pytest.approx(0.0)
    assert s.feed_in_kwh == pytest.approx(0.0)
    assert s.e_after == pytest.approx(
        5.0 + 0.5 * battery.efficiency_charge - 0.25 / battery.efficiency_discharge
    )


def test_spike_comes_from_grid_when_holding(battery, tariff):
    (slot,) = scenario((1, 50, 3.0, 2.0, 1.0))
    s = step(5.0, slot, Action.HOLD, battery, tariff)
    assert s.import_kwh == pytest.approx(0.25)
    assert s.e_after == pytest.approx(5.0 + 0.5 * battery.efficiency_charge)
    assert s.cost_ct == pytest.approx(0.25 * 50)


def test_hold_charges_from_spikes_during_import(battery, tariff):
    """Import on average, but short PV peaks charge the battery even while holding."""
    (slot,) = scenario((1, 50, 1.0, 2.0, 0.4))
    s = step(5.0, slot, Action.HOLD, battery, tariff)
    assert s.e_after == pytest.approx(5.0 + 0.1 * battery.efficiency_charge)
    assert s.import_kwh == pytest.approx(0.25 + 0.1)


def test_grid_charge_spike_comes_from_grid(battery, tariff):
    """Even with a full battery: in charge mode it does not discharge, spikes cost grid energy."""
    (slot,) = scenario((1, 50, 3.0, 2.0, 1.0))
    s = step(battery.e_max, slot, Action.GRID_CHARGE, battery, tariff)
    assert s.import_kwh == pytest.approx(0.25)
    assert s.feed_in_kwh == pytest.approx(0.5)


def test_no_pv_no_spike():
    from ems.core.forecast import with_spikes

    slots = with_spikes(scenario((1, 40, 0.0, 1.0), (1, 40, 2.0, 1.0), (1, 40, 0.5, 1.0)), share=0.25)
    assert [s.spike_kw for s in slots] == [0.0, 0.25, 0.125]


# --- whole plans -----------------------------------------------------------------------------


def test_residual_value_prevents_emptying(battery, tariff):
    slots = scenario((4, 40, 0.0, 1.0))
    result = simulate(slots, 8.0, [Action.HOLD] * 4, battery, tariff)
    assert result.residual_ct > 0
    assert result.valuation_ct < result.cost_ct


def test_wrong_length_is_rejected(battery, tariff):
    with pytest.raises(ValueError):
        simulate(scenario((3, 40, 0, 1)), 5.0, [Action.NORMAL], battery, tariff)


# --- properties for arbitrary inputs (hypothesis) --------------------------------------------

slot_st = st.tuples(
    st.floats(-10, 100),  # Preis ct
    st.floats(0, 15),  # PV kW
    st.floats(0, 12),  # Verbrauch kW
    st.one_of(st.just(0.0), st.floats(0, 3)),  # Spitze kW
)


@st.composite
def plans(draw):
    raw = draw(st.lists(slot_st, min_size=1, max_size=40))
    slots = scenario(*[(1, *r) for r in raw])
    actions = draw(st.lists(st.sampled_from(list(Action)), min_size=len(slots), max_size=len(slots)))
    battery = Battery()
    e0 = draw(st.floats(battery.e_min, battery.e_max))
    return slots, actions, e0


@given(plans())
def test_content_stays_within_bounds(plan):
    slots, actions, e0 = plan
    battery = Battery()
    for e in simulate(slots, e0, actions, battery, Tariff()).soc:
        assert battery.e_min - EPS <= e <= battery.e_max + EPS


@given(plans())
def test_energy_balance(plan):
    """Grid = consumption − PV + what goes into the battery − what comes out (after losses)."""
    slots, actions, e0 = plan
    battery = Battery()
    for slot, s in zip(slots, simulate(slots, e0, actions, battery, Tariff()).steps, strict=True):
        out_ac_kwh = (s.discharged_kwh - s.standby_kwh) * battery.efficiency_discharge
        eta_in = (
            battery.efficiency_grid_charge if s.action is Action.GRID_CHARGE else battery.efficiency_charge
        )
        in_ac_kwh = (s.e_after - s.e_before + s.discharged_kwh) / eta_in
        grid = s.import_kwh - s.feed_in_kwh
        assert grid == pytest.approx((slot.load_kw - slot.pv_kw) * DT_H + in_ac_kwh - out_ac_kwh, abs=1e-9)
        assert s.import_kwh >= -EPS and s.feed_in_kwh >= -EPS and in_ac_kwh >= -EPS
        if slot.spike_kw == 0:  # without a spike never import and export at the same time
            assert not (s.import_kwh > EPS and s.feed_in_kwh > EPS)


@given(plans())
def test_hold_never_discharges(plan):
    slots, _, e0 = plan
    battery = Battery()
    result = simulate(slots, e0, [Action.HOLD] * len(slots), battery, Tariff())
    for s in result.steps:
        assert s.e_after >= s.e_before - EPS
        assert s.discharged_kwh == 0


# --- cross-check with the old Jinja planner (private cases) -----------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "2026-11-08_0500",
        pytest.param(
            "2026-11-08_1315",
            marks=pytest.mark.xfail(
                strict=True,
                reason="Old planner: 19:45 planned as 'normal' but only partly discharged "
                "(rest kept for the next day). The inverter cannot do that, "
                "it would discharge fully.",
            ),
        ),
    ],
)
def test_cross_check_old_planner(name):
    """In quarter hours without an action the new simulation must give the same SoC as the old
    planner (its SoC is rounded to whole %). Computed with its assumptions."""
    case = load_case(name)
    battery = BATTERY_OLD_PLANNER
    soc = case.old_plan_soc_percent
    active = set()
    for a in case.old_plan_actions:
        for i, slot in enumerate(case.slots):
            if a["von"] <= slot.start.isoformat() < a["bis"]:
                active.add(i)
    checked = 0
    for i in range(1, len(case.slots)):
        if i in active:
            continue
        e = soc[i - 1] / 100 * battery.capacity_kwh
        s = step(e, case.slots[i], Action.NORMAL, battery, Tariff())
        new = max(s.e_after, battery.e_min) / battery.capacity_kwh * 100
        assert new == pytest.approx(soc[i], abs=1.5), f"quarter hour {i} ({case.slots[i].start:%H:%M})"
        checked += 1
    assert checked > 50


def test_slot_is_immutable():
    slot = scenario((1, 1, 1, 1))[0]
    with pytest.raises(AttributeError):
        slot.price_ct = 2  # type: ignore[misc]
    assert isinstance(slot, Slot)


@given(slot_st, st.sampled_from(list(Action)), st.lists(st.floats(0, 11), min_size=1, max_size=20))
def test_vector_matches_scalar(raw, action, contents):
    """The optimizer uses step_vector, the valuation uses step: both must agree."""
    (slot,) = scenario((1, *raw))
    battery, tariff = Battery(), Tariff(feed_in_ct=7.0)
    e_new, cost = step_vector(np.array(contents), slot, action, battery, tariff)
    for i, e in enumerate(contents):
        s = step(e, slot, action, battery, tariff)
        assert e_new[i] == pytest.approx(s.e_after, abs=1e-12)
        assert cost[i] == pytest.approx(s.cost_ct, abs=1e-9)
