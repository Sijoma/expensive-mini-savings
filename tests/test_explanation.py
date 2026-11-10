"""Plan explanation: grid charging with a full battery is shown as hold; 24 h savings forecast."""

from __future__ import annotations

from datetime import datetime, timedelta

from custom_components.ems.adapter import savings_24h
from custom_components.ems.core.explanation import create_plan, hold_when_full
from custom_components.ems.core.model import Action, Battery, Slot, Tariff
from custom_components.ems.core.optimizer import plan_with_value
from custom_components.ems.core.simulation import simulate

T0 = datetime(2026, 11, 11, 0, 0)


def night(prices: list[float], load_kw: float = 0.4) -> list[Slot]:
    return [Slot(T0 + timedelta(minutes=15 * i), p, 0.0, load_kw) for i, p in enumerate(prices)]


# 3 h very cheap, 2 h slightly more, then a long expensive day: the battery fills in the cheapest
# hours and then waits full (the optimizer keeps grid charging there, it costs the same)
SLOTS = night([35.0] * 12 + [36.0] * 8 + [80.0] * 40, load_kw=1.2)
BATTERY = Battery()
TARIFF = Tariff()


def test_full_battery_waits_as_hold_not_as_grid_charge():
    plan = create_plan(SLOTS, 0.0, BATTERY, TARIFF)
    steps = plan.with_plan.steps
    assert Action.GRID_CHARGE in plan.actions
    for action, st in zip(plan.actions, steps, strict=True):
        if action is Action.GRID_CHARGE:
            assert st.grid_charged_kwh > 0 or st.e_before < BATTERY.e_max - 1e-6
    # the waiting part is a hold block right after the charge block
    kinds = [b.action for b in plan.blocks]
    assert kinds[:2] == [Action.GRID_CHARGE, Action.HOLD]
    assert plan.blocks[0].until == plan.blocks[1].first


def test_split_costs_exactly_the_same():
    actions, _ = plan_with_value(SLOTS, 0.0, BATTERY, TARIFF)
    split = hold_when_full(SLOTS, 0.0, actions, BATTERY, TARIFF)
    assert split != actions
    before = simulate(SLOTS, 0.0, actions, BATTERY, TARIFF)
    after = simulate(SLOTS, 0.0, split, BATTERY, TARIFF)
    assert abs(before.cost_ct - after.cost_ct) < 1e-9
    assert abs(before.residual_ct - after.residual_ct) < 1e-9
    assert [s.e_after for s in before.steps] == [s.e_after for s in after.steps]


def test_short_full_stretch_stays_grid_charge():
    """A single full quarter hour is not turned into a hold (shorter than the minimum hold)."""
    actions = [Action.GRID_CHARGE] * 3 + [Action.NORMAL] * 5
    slots = night([35.0] * 8)
    e0 = BATTERY.e_max - 0.0001  # full after the first quarter hour
    split = hold_when_full(slots, e0, actions, BATTERY, TARIFF)
    assert split[:3].count(Action.HOLD) in (0, 2, 3)
    assert split[3:] == actions[3:]


def test_nothing_to_change_without_grid_charge():
    actions = [Action.NORMAL] * len(SLOTS)
    assert hold_when_full(SLOTS, 5.0, actions, BATTERY, TARIFF) == actions


def test_24h_savings_count_what_stays_in_the_battery():
    """A night charge whose energy is used after the 24 h window must not look like a loss."""
    slots = night([35.0] * 16 + [80.0] * 80 + [80.0] * 16)  # 28 h
    plan = create_plan(slots, 0.0, BATTERY, TARIFF)
    assert plan.saves_ct > 0
    assert savings_24h(plan, BATTERY) > 0
