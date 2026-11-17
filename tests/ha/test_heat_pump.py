"""The heat pump PV boost in a test Home Assistant: switching over a simulated ISG, modes, foreign settings,
the former automation, restart.

The ISG is simulated: switch.turn_on / switch.turn_off set the switch states (unless `stuck`)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from homeassistant.core import ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from test_integration import ENTITIES

from custom_components.ems import heat_pump_control as hp
from custom_components.ems.heat_pump_executor import LEGACY_AUTOMATION

ACTIVE = "switch.wp_sg_ready_aktiv"
BOOST = "switch.wp_sg_ready_eingang_1"
BLOCK = "switch.wp_sg_ready_eingang_2"
MODE = "select.wp_betriebsart"
GRID = "sensor.meter_active_power"
BATTERY = "sensor.battery_power"
START = "2026-11-17T12:00:00+01:00"


@pytest.fixture
def isg(hass, freezer):
    freezer.move_to(START)  # before the first switch states: their last_changed must not lie ahead
    calls: list[tuple[str, bool]] = []
    sim = {"stuck": False}

    def init(active="on", boost="off", block="off", mode="program"):
        for eid, value in ((ACTIVE, active), (BOOST, boost), (BLOCK, block), (MODE, mode)):
            hass.states.async_set(eid, value)

    async def turn(call: ServiceCall, on: bool) -> None:
        for eid in (
            [call.data["entity_id"]] if isinstance(call.data["entity_id"], str) else call.data["entity_id"]
        ):
            calls.append((eid, on))
            if not sim["stuck"]:
                hass.states.async_set(eid, "on" if on else "off")

    async def turn_on(call: ServiceCall) -> None:
        await turn(call, True)

    async def turn_off(call: ServiceCall) -> None:
        await turn(call, False)

    def install() -> None:
        hass.services.async_register("switch", "turn_on", turn_on)
        hass.services.async_register("switch", "turn_off", turn_off)

    init()
    return {"calls": calls, "init": init, "sim": sim, "install": install}


@pytest.fixture
async def ems(hass, set_states, tmp_path, isg, freezer):
    hass.config.config_dir = str(tmp_path)
    await hass.config.async_set_time_zone("Europe/Berlin")
    set_states(**{"sensor.battery_level": "95.0"})
    hass.states.async_set(GRID, "0", {"unit_of_measurement": "W"})
    hass.states.async_set(BATTERY, "0", {"unit_of_measurement": "W"})
    conf = {
        k: v
        for k, v in ENTITIES.items()
        if k not in ("verschleiss", "netzladeleistung", "soll", "lastprofil")
    }
    conf |= {"wp_sg_ready": ACTIVE, "wp_verstaerkt": BOOST, "wp_sperre": BLOCK, "wp_betriebsart": MODE}
    assert await async_setup_component(
        hass, "ems", {"ems": {**conf, "aufzeichnung": False, "messung": False}}
    )
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries("ems")
    isg["install"]()
    isg["init"]()
    freezer.tick(timedelta(minutes=31))  # the switches were set long ago (no pause after a boost)
    return entry.runtime_data.executor


async def export_for(hass, ems, freezer, minutes: int, watt: float = -2000) -> None:
    """Export (negative grid power) for some minutes; the timers see every value."""
    hass.states.async_set(GRID, str(watt), {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    for _ in range(minutes):
        freezer.tick(timedelta(minutes=1))
        ems.heat_pump.power_changed()
    await hass.async_block_till_done()


async def test_entities_and_plan_only_by_default(hass, ems, isg, freezer):
    assert hass.states.get("select.ems_modus_waermepumpe").state == hp.PLAN_ONLY
    await export_for(hass, ems, freezer, 11)
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    assert isg["calls"] == []  # "Nur planen" switches nothing ...
    ems.heat_pump.every_minute(dt_util.now())
    await hass.async_block_till_done()
    target = hass.states.get("sensor.ems_waermepumpe_soll")
    assert target.state == hp.BOOST  # ... but shows what the rules want
    assert target.attributes["ist"] == hp.NORMAL
    assert target.attributes["grund"] == "Seit 10 min mehr als 1200 W Einspeisung"


async def test_automatic_boost_on_in_order_and_off_after_import(hass, ems, isg, freezer):
    await ems.heat_pump.set_mode(hp.AUTOMATIC)
    await export_for(hass, ems, freezer, 9)
    await ems.heat_pump.apply("pruefen")
    assert isg["calls"] == []  # 9 min are not enough
    await export_for(hass, ems, freezer, 1)
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    assert isg["calls"] == [(BLOCK, False), (ACTIVE, True), (BOOST, True)]
    assert ems.heat_pump.state["set_by_ems"] is True
    assert ems.state["log"][0]["text"].startswith("Wärmepumpe: Normal → PV-Boost")

    isg["calls"].clear()
    hass.states.async_set(GRID, "500", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    for _ in range(5):
        freezer.tick(timedelta(minutes=1))
        ems.heat_pump.power_changed()
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    assert isg["calls"] == [(BOOST, False), (BLOCK, False)]
    assert ems.heat_pump.state["set_by_ems"] is False
    assert hass.states.get(ACTIVE).state == "on"  # SG Ready stays enabled (state 2 = normal)


async def test_passive_while_the_former_automation_is_on(hass, ems, isg, freezer):
    hass.states.async_set(LEGACY_AUTOMATION, "on")
    await ems.heat_pump.set_mode(hp.AUTOMATIC)
    await export_for(hass, ems, freezer, 11)
    await ems.heat_pump.apply("pruefen")
    assert isg["calls"] == []
    assert [e["text"] for e in ems.state["log"]].count(
        "Wärmepumpe: Alte PV-Boost-Automation ist noch an, das EMS schaltet nicht."
    ) == 1
    hass.states.async_set(LEGACY_AUTOMATION, "off")  # switched off: the EMS takes over
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    assert (BOOST, True) in isg["calls"]


async def test_foreign_block_and_boost_are_never_touched(hass, ems, isg, freezer):
    await ems.heat_pump.set_mode(hp.AUTOMATIC)
    isg["init"](block="on")
    await export_for(hass, ems, freezer, 11)
    await ems.heat_pump.apply("pruefen")
    await ems.heat_pump.apply("pruefen")
    assert isg["calls"] == []
    assert [e["art"] for e in ems.state["log"]].count("Manuell") == 1  # reported once
    isg["init"](boost="on")  # someone boosts by hand, then import comes: the EMS leaves it
    hass.states.async_set(GRID, "800", {"unit_of_measurement": "W"})
    for _ in range(6):
        freezer.tick(timedelta(minutes=1))
        ems.heat_pump.power_changed()
    await ems.heat_pump.apply("pruefen")
    assert isg["calls"] == []


async def test_back_to_plan_only_takes_back_the_own_boost(hass, ems, isg, freezer):
    await ems.heat_pump.set_mode(hp.AUTOMATIC)
    await export_for(hass, ems, freezer, 11)
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    isg["calls"].clear()
    entity = hass.data["entity_components"]["select"].get_entity("select.ems_modus_waermepumpe")
    await entity.async_select_option(hp.PLAN_ONLY)
    await hass.async_block_till_done()
    assert isg["calls"] == [(BOOST, False), (BLOCK, False)]
    assert hass.states.get("select.ems_modus_waermepumpe").state == hp.PLAN_ONLY


async def test_boost_ended_by_hand_is_forgotten_and_waits_30_min(hass, ems, isg, freezer):
    await ems.heat_pump.set_mode(hp.AUTOMATIC)
    await export_for(hass, ems, freezer, 11)
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    isg["calls"].clear()
    hass.states.async_set(BOOST, "off")  # by hand
    await ems.heat_pump.apply("pruefen")
    assert ems.heat_pump.state["set_by_ems"] is False
    assert ems.state["log"][0]["text"] == "Wärmepumpe: PV-Boost wurde von außen beendet"
    await export_for(hass, ems, freezer, 29)
    await ems.heat_pump.apply("pruefen")
    assert isg["calls"] == []  # the pause after a boost
    await export_for(hass, ems, freezer, 2)
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    assert (BOOST, True) in isg["calls"]


async def test_unconfirmed_switch_is_an_error_and_retried(hass, ems, isg, freezer):
    await ems.heat_pump.set_mode(hp.AUTOMATIC)
    isg["sim"]["stuck"] = True
    await export_for(hass, ems, freezer, 11)
    task = hass.async_create_task(ems.heat_pump.apply("pruefen"))
    for _ in range(50):  # until the confirmation waits, then let its 60 s pass
        if task.done():
            break
        await asyncio.sleep(0)
        freezer.tick(timedelta(seconds=61))
        async_fire_time_changed(hass, dt_util.utcnow())
    await asyncio.wait_for(task, 5)
    assert ems.state["log"][0]["art"] == "Fehler"
    assert ems.heat_pump.state["switch_failed"] is True
    isg["sim"]["stuck"] = False
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    assert ems.heat_pump.state["set_by_ems"] is True and ems.heat_pump.state["switch_failed"] is False


async def test_own_boost_survives_a_restart(hass, ems, isg, freezer):
    await ems.heat_pump.set_mode(hp.AUTOMATIC)
    await export_for(hass, ems, freezer, 11)
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    await ems.store.async_save(ems.state)
    ems.state["heat_pump"] = {}
    await ems.async_load()
    assert ems.heat_pump.state["set_by_ems"] is True and ems.heat_pump.mode == hp.AUTOMATIC
    isg["calls"].clear()
    freezer.move_to("2026-11-17T17:00:30+01:00")
    await ems.heat_pump.apply("pruefen")
    await hass.async_block_till_done()
    assert isg["calls"] == [(BOOST, False), (BLOCK, False)]  # its own: off at 17:00


async def test_without_heat_pump_keys_nothing_is_created(hass, set_states, tmp_path):
    hass.config.config_dir = str(tmp_path)
    set_states(**{"sensor.battery_level": "45.0"})
    assert await async_setup_component(hass, "ems", {"ems": {**ENTITIES, "aufzeichnung": False}})
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries("ems")
    assert entry.runtime_data.executor.heat_pump is None
    assert hass.states.get("select.ems_modus_waermepumpe") is None
    assert hass.states.get("sensor.ems_waermepumpe_soll") is None
