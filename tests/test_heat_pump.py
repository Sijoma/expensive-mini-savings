"""Heat pump PV boost."""

from __future__ import annotations

from conftest import scenario
from ems.core.explanation import create_plan
from ems.core.heat_pump import plan_boost
from ems.core.model import Battery, Tariff


def test_no_boost_when_battery_takes_the_surplus():
    """Battery almost empty: the surplus goes into the battery, nothing would be exported."""
    slots = scenario((8, 40, 0.0, 1.0), (8, 40, 2.0, 1.0), (8, 40, 0.0, 1.0))
    plan = create_plan(slots, 0.5, Battery(), Tariff())
    assert not any(plan.boost)


def test_boost_with_full_battery_in_sunniest_quarters():
    slots = scenario((4, 40, 3.0, 1.0), (4, 40, 6.0, 1.0), (4, 40, 3.0, 1.0))
    battery = Battery()
    plan = create_plan(slots, battery.e_max, battery, Tariff())
    exported = sum(s.feed_in_kwh for s in plan.with_plan.steps)
    expected = int(exported * 0.8 / 0.25)
    assert sum(plan.boost) == min(expected, 12)
    # the four sunniest (6 kW) first
    assert all(plan.boost[4:8])


def test_boost_only_in_surplus_phases():
    slots = scenario((4, 40, 6.0, 1.0), (4, 40, 0.0, 1.0), (4, 40, 6.0, 1.0))
    battery = Battery()
    plan = create_plan(slots, battery.e_max, battery, Tariff())
    assert not any(plan.boost[4:8])
    assert any(plan.boost[:4]) and any(plan.boost[8:])


def test_plan_boost_per_phase():
    slots = scenario((2, 40, 2.0, 1.0), (1, 40, 0.0, 1.0), (2, 40, 2.0, 1.0))

    class S:
        def __init__(self, e):
            self.feed_in_kwh = e

    steps = [S(0.25), S(0.25), S(0.0), S(0.1), S(0.1)]
    # phase 1: 0.5 kWh × 0.8 / 0.25 = 1 quarter hour; phase 2: 0.2 × 0.8 / 0.25 = 0
    assert plan_boost(slots, steps) == [True, False, False, False, False]
