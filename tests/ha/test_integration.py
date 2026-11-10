"""The integration in a real (test) Home Assistant: setup, sensors, recording."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

PLAN = "sensor.ems_plan_akku"
ENTITIES = {
    "preise": "sensor.electricity_prices",
    "pv_prognose": "sensor.pv_forecast",
    "lastprofil": "sensor.load_profile",
    "verschleiss": "input_number.battery_wear",
    "netzladeleistung": "input_number.grid_charge_power",
    "soll": "sensor.battery_target",
    "preis_jetzt": "sensor.electricity_price_now",
}
"""The example inputs (tests/example) use these names; only the Sungrow entities have defaults."""
VALUE = "sensor.ems_wert_akku"


@pytest.fixture
async def set_up(hass, freezer, set_states, now, tmp_path):
    hass.config.config_dir = str(tmp_path)
    freezer.move_to(now)
    await hass.config.async_set_time_zone("Europe/Berlin")
    set_states(**{"sensor.battery_level": "45.0"})
    assert await async_setup_component(hass, "ems", {"ems": ENTITIES})
    await hass.async_block_till_done()
    return hass


async def test_sensors_are_created(set_up):
    hass = set_up
    plan = hass.states.get(PLAN)
    assert plan is not None
    assert plan.state in ("Eigenverbrauch", "Entladung gesperrt", "Laden aus dem Netz")
    attr = plan.attributes
    assert len(attr["slots"]) == 6 + 96
    assert attr["berechnet"].startswith("2026-11-08T22:31")
    assert isinstance(attr["grund"], str)
    assert float(hass.states.get(VALUE).state) >= 0
    wp = hass.states.get("sensor.ems_plan_warmepumpe")
    assert wp.state in ("Normal", "PV-Boost")
    assert isinstance(wp.attributes["aktionen"], list)


async def test_recording_is_written(set_up):
    hass = set_up
    file_path = Path(hass.config.path("ems", "aufzeichnung", "2026-11-08.jsonl"))
    lines = [json.loads(z) for z in file_path.read_text().splitlines()]
    assert len(lines) == 1
    assert lines[0]["soc"] == 45.0
    assert len(lines[0]["slots"]) == 102
    assert len(lines[0]["aktionen"]) == 102


async def test_new_prices_trigger_replanning(set_up, ha_states):
    hass = set_up
    before = hass.states.get(PLAN).attributes["slots"][0][1]
    prices = [[t, 99.0] for t, _ in ha_states["sensor.electricity_prices"]["attributes"]["preise"]]
    hass.states.async_set("sensor.electricity_prices", "192", {"preise": prices})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=15))  # wait for the debounce
    await hass.async_block_till_done()
    assert hass.states.get(PLAN).attributes["slots"][0][1] == 99.0 != before


async def test_unavailable_without_prices(hass, freezer, set_states, now, tmp_path):
    hass.config.config_dir = str(tmp_path)
    freezer.move_to(now)
    set_states(**{"sensor.electricity_prices": None})
    assert await async_setup_component(hass, "ems", {"ems": ENTITIES})
    await hass.async_block_till_done()
    plan = hass.states.get(PLAN)
    assert plan is None or plan.state == "unavailable"


async def test_custom_entities_in_configuration(hass, freezer, set_states, now):
    freezer.move_to(now)
    set_states()
    hass.states.async_set("sensor.my_soc", "80", {})
    assert await async_setup_component(
        hass, "ems", {"ems": {**ENTITIES, "soc": "sensor.my_soc", "aufzeichnung": False}}
    )
    await hass.async_block_till_done()
    assert hass.states.get(PLAN).attributes["slots"][0][4] >= 75


async def test_measurements_per_quarter(hass, freezer, set_states, now, tmp_path):
    """Power values change; at the quarter-hour boundary one line is written."""
    hass.config.config_dir = str(tmp_path)
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(now.replace(minute=30, second=0))  # 22:30:00
    set_states(**{"sensor.battery_level": "40.0"})
    for eid, w in [
        ("sensor.load_power", "1000"),
        ("sensor.total_dc_power", "0"),
        ("sensor.battery_power", "1000"),
        ("sensor.meter_active_power", "0"),
        ("sensor.electricity_price_now", "0.512"),
        ("select.ems_mode", "Self-consumption mode (default)"),
        ("select.battery_forced_charge_discharge", "Stop (default)"),
    ]:
        hass.states.async_set(eid, w)
    assert await async_setup_component(hass, "ems", {"ems": {**ENTITIES, "aufzeichnung": False}})
    await hass.async_block_till_done()

    freezer.tick(timedelta(minutes=5))  # 22:35: battery held, energy from the grid
    hass.states.async_set("sensor.battery_power", "0")
    hass.states.async_set("sensor.meter_active_power", "1000")
    hass.states.async_set("number.battery_max_discharge_power", "10.0")
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=2, seconds=30))  # 22:37:30 middle
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=7, seconds=30))  # 22:45:00 Grenze
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    file_path = Path(tmp_path, "ems", "messung", "2026-11-08.jsonl")
    (line,) = [json.loads(z) for z in file_path.read_text().splitlines()]
    assert line["start"] == "2026-11-08T22:30:00+01:00"
    assert line["last_kw"] == pytest.approx(1.0)
    assert line["akku_entladen_kwh"] == pytest.approx(1.0 * 5 / 60, abs=1e-3)
    assert line["netz_bezug_kwh"] == pytest.approx(1.0 * 10 / 60, abs=1e-3)
    assert line["soc_prozent"] == 40.0
    assert line["preis_ct"] == 51.2
    assert line["aktion"] == "h"
    assert line["abgedeckt_s"] == 900
