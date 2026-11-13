"""HA states → slots and plan → attributes (without Home Assistant)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest

from conftest import EXAMPLE, load_case
from ems.adapter import build_slots, plan_attributes, running_action
from ems.core.explanation import create_plan
from ems.core.forecast import PV_EFFICIENCY, prepare
from ems.core.model import Action, Battery, Tariff

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 8, 22, 31, 7, tzinfo=TZ)


@pytest.fixture(scope="module")
def ha_states():
    return json.loads((EXAMPLE / "ha_eingaben.json").read_text())["zustaende"]


def slots_from(ha_states, now=NOW):
    return build_slots(
        ha_states["sensor.electricity_prices"]["attributes"]["preise"],
        ha_states["sensor.pv_forecast"]["attributes"]["forecast"],
        ha_states["sensor.load_profile"]["attributes"]["profil"],
        now,
    )


def test_horizon_from_current_quarter_to_end_of_prices(ha_states):
    slots = slots_from(ha_states)
    assert slots[0].start == datetime(2026, 11, 8, 22, 30, tzinfo=TZ)
    assert slots[-1].start == datetime(2026, 11, 9, 23, 45, tzinfo=TZ)
    assert len(slots) == 6 + 96
    assert all(b.start - a.start == timedelta(minutes=15) for a, b in pairwise(slots))


def test_load_from_profile_times_factor(ha_states):
    profile = ha_states["sensor.load_profile"]["attributes"]["profil"]
    s = slots_from(ha_states)[0]
    assert s.load_kw == pytest.approx(profile[22] / 1000 * 1.1)


def test_noon_pv_converted_to_ac_with_p10(ha_states):
    forecast = {f["datetime"]: f for f in ha_states["sensor.pv_forecast"]["attributes"]["forecast"]}
    noon = next(s for s in slots_from(ha_states) if s.start.hour == 12 and s.start.minute == 0)
    f = forecast[noon.start.isoformat()]
    p10 = f["p10"] if f["p10"] is not None else f["watts"]
    expected = (0.85 * f["watts"] + 0.15 * p10) / 1000 * PV_EFFICIENCY
    assert noon.pv_kw == pytest.approx(expected)
    assert noon.spike_kw > 0


def test_broken_inputs_give_no_slots():
    assert build_slots(None, None, None, NOW) == []
    assert build_slots([[0, 1]], [{"datetime": "kaputt"}], [1, 2], NOW) == []


def test_plannable_without_pv_forecast(ha_states):
    slots = build_slots(ha_states["sensor.electricity_prices"]["attributes"]["preise"], None, None, NOW)
    assert len(slots) == 102
    assert all(s.pv_kw == 0 for s in slots)


@pytest.mark.parametrize(
    ("target", "minutes_ago", "expected"),
    [
        ("Eigenverbrauch", 60, (None, 0)),
        ("Entladung gesperrt", 5, (Action.HOLD, 1)),
        ("Entladung gesperrt", 20, (Action.HOLD, 2)),
        ("Laden aus dem Netz", 50, (Action.GRID_CHARGE, 4)),
        ("unavailable", 5, (None, 0)),
        (None, 0, (None, 0)),
    ],
)
def test_running_action(target, minutes_ago, expected):
    assert running_action(target, NOW - timedelta(minutes=minutes_ago), NOW) == expected


def test_attributes_keep_the_interface(ha_states):
    """Contract with dashboard and YAML executor: same keys and slots format."""
    battery = Battery()
    plan = create_plan(slots_from(ha_states), 2.0, battery, Tariff())
    attr = plan_attributes(plan, battery, NOW, 12.3)
    old = {"von", "bis", "was", "kwh", "ct", "spart", "netz_kwh", "kurz", "fuer_ct", "vorteil"}
    assert {"ersparnis_24h", "netzladen_kwh", "berechnet", "bis"} <= attr.keys()
    assert attr["aktionen"]
    for a in attr["aktionen"]:
        assert old <= a.keys()
        if a["was"] == "h":
            assert a["fuer_ct"] > a["ct"]  # kept for a more expensive time
        assert a["was"] in ("h", "l")
        assert a["bis"] > a["von"]
        assert "ct" in a["grund"]
    for row, sl in zip(attr["slots"], plan.slots, strict=True):
        assert len(row) == 16
        pv_house, battery_house, grid_house, pv_battery, pv_grid, _grid_battery = row[10:]
        assert pv_house + battery_house + grid_house == pytest.approx(sl.load_kw, abs=0.03)
        assert pv_house + pv_battery + pv_grid == pytest.approx(sl.pv_kw, abs=0.15)  # Spitzen laufen brutto
        assert min(row[10:]) >= 0
        assert isinstance(row[0], int) and row[5] in ("n", "h", "l") and 0 <= row[4] <= 100
        assert row[6] in ("n", "pv")
    for w in attr["wp_aktionen"]:
        assert w["was"] == "pv" and w["bis"] > w["von"]
    assert json.dumps(attr)  # muss sich speichern lassen
    assert len(json.dumps(attr["slots"])) < 16000  # recorder attribute limit, in case it is recorded


def test_blocks_save_and_have_reasons():
    case = load_case("2026-11-08_1000")
    battery = Battery()
    plan = create_plan(prepare(case.slots), case.soc_percent / 100 * battery.capacity_kwh, battery, Tariff())
    assert plan.blocks
    for b in plan.blocks:
        assert b.saves_ct > 0
        if b.action is Action.HOLD:
            assert b.value_ct > b.now_ct  # that is why it is kept
    assert plan.reason_now.startswith("Strom kostet in dieser Zeit")
    assert all(b.calculation for b in plan.blocks)
    assert " ct" in plan.reason_now and "für " in plan.reason_now


def test_prices_from_tibber():
    from ems.adapter import prices_from_tibber

    start = datetime(2026, 11, 8, 0, 0, tzinfo=TZ)
    response = {
        "prices": {
            "Home": [
                {"start_time": (start + timedelta(minutes=15)).isoformat(), "price": 0.3051},
                {"start_time": start, "price": 0.2999},
                {"start_time": "kaputt", "price": 0.5},
                {"price": 0.5},
            ],
            "Other home": [{"start_time": start.isoformat(), "price": 9.0}],
        }
    }
    ms = int(start.timestamp() * 1000)
    assert prices_from_tibber(response) == [[ms, 29.99], [ms + 900_000, 30.51]]
    assert prices_from_tibber(None) == []
    assert prices_from_tibber({"prices": {}}) == []


def test_price_at():
    from ems.adapter import price_at

    start = datetime(2026, 11, 8, 22, 0, tzinfo=TZ)
    ms = int(start.timestamp() * 1000)
    prices = [[ms, 30.0], [ms + 900_000, 31.5]]
    assert price_at(prices, start + timedelta(minutes=14, seconds=59)) == 30.0
    assert price_at(prices, start + timedelta(minutes=15)) == 31.5
    assert price_at(prices, start + timedelta(minutes=30)) is None
    assert price_at(None, start) is None


def test_plan_blocks_are_the_blocks_of_the_attribute(ha_states):
    """The executor and the sensor see the same blocks; the external keys and their order stay fixed."""
    from ems.adapter import plan_blocks

    battery = Battery()
    plan = create_plan(slots_from(ha_states), 2.0, battery, Tariff())
    blocks = plan_blocks(plan, battery)
    attr = plan_attributes(plan, battery, NOW, 1.0)
    assert blocks and attr["aktionen"] == [b.as_attribute() for b in blocks]
    assert list(attr["aktionen"][0]) == [
        "von", "bis", "was", "kwh", "ct", "wert_ct", "fuer_ct", "vorteil",
        "spart", "netz_kwh", "kurz", "grund", "rechnung",
    ]  # fmt: skip
    for b in blocks:
        assert b.end_ms - b.start_ms >= 900_000
        assert (b.grid_kwh is None) == (b.code == "l") and (b.gain_ct is None) == (b.code == "h")
