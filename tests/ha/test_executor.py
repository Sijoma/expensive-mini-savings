"""The executor in a test Home Assistant: modes, switching the inverter, approvals, booking, takeover.

The inverter is simulated: select.select_option / number.set_value set the entity states the way the
Sungrow Modbus integration reports them back."""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.core import ServiceCall
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from test_integration import ENTITIES

from custom_components.ems import control as c

MODE = "select.ems_mode"
FORCED = "select.battery_forced_charge_discharge"
DISCHARGE = "number.battery_max_discharge_power"
POWER = "number.battery_forced_charge_discharge_power"


@pytest.fixture
def inverter(hass):
    """A simulated Sungrow: records commands, reports new values back (unless `stuck`)."""
    calls: list[tuple[str, object]] = []
    sim = {"stuck": False}

    def init(mode=c.SELF_CONSUMPTION, forced=c.FORCED_STOP, discharge=5900):
        hass.states.async_set(MODE, mode)
        hass.states.async_set(FORCED, forced)
        hass.states.async_set(DISCHARGE, str(discharge), {"max": 5900})
        hass.states.async_set(POWER, "5000")

    async def select(call: ServiceCall) -> None:
        calls.append((call.data["entity_id"], call.data["option"]))
        if not sim["stuck"]:
            hass.states.async_set(call.data["entity_id"], call.data["option"])

    async def number(call: ServiceCall) -> None:
        calls.append((call.data["entity_id"], call.data["value"]))
        if not sim["stuck"]:
            hass.states.async_set(call.data["entity_id"], str(call.data["value"]), {"max": 5900})

    def install() -> None:
        """Register the simulated services. Must run after the integration is set up: loading the
        select and number components would otherwise replace them with the real ones."""
        hass.services.async_register("select", "select_option", select)
        hass.services.async_register("number", "set_value", number)

    init()
    return {"calls": calls, "init": init, "sim": sim, "install": install}


@pytest.fixture
async def ems(hass, set_states, tmp_path, inverter):
    hass.config.config_dir = str(tmp_path)
    await hass.config.async_set_time_zone("Europe/Berlin")
    set_states(**{"sensor.battery_level": "45.0"})
    for eid, value in [
        ("sensor.total_battery_charge", "100.0"),
        ("sensor.total_battery_charge_from_pv", "60.0"),
        ("sensor.total_imported_energy", "500.0"),
        ("sensor.total_consumed_energy", "2000.0"),
    ]:
        hass.states.async_set(eid, value)
    conf = {
        k: v
        for k, v in ENTITIES.items()
        if k not in ("verschleiss", "netzladeleistung", "soll", "lastprofil")
    }
    assert await async_setup_component(
        hass, "ems", {"ems": {**conf, "aufzeichnung": False, "messung": False}}
    )
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries("ems")
    inverter["install"]()
    return entry.runtime_data.executor


def block_now(was: str, *, minutes_ago=5, length=60, kwh=0.0, spart=0.5, netz=1.0) -> dict:
    von = int((dt_util.now() - timedelta(minutes=minutes_ago)).timestamp() * 1000)
    return {"von": von, "bis": von + length * 60_000, "was": was, "kwh": kwh, "ct": 40.0, "spart": spart,
            "netz_kwh": netz, "grund": "Test"}  # fmt: skip


def with_plan(ex, monkeypatch, blocks):
    """A current plan with these blocks (the example inputs in the test HA are from an earlier day)."""
    attribute = {"aktionen": blocks, "berechnet": dt_util.now().isoformat(), "slots": []}
    monkeypatch.setattr(ex.coordinator, "data", SimpleNamespace(attribute=attribute))
    monkeypatch.setattr(ex, "blocks", lambda: blocks)
    monkeypatch.setattr(ex, "plan_fresh", lambda now: True)


async def test_mode_select_entity(hass, ems):
    """The select entity changes the mode (the simulated inverter replaces select.select_option, so call
    the entity directly)."""
    entity = hass.data["entity_components"]["select"].get_entity("select.ems_modus_akku")
    await entity.async_select_option(c.WITH_APPROVAL)
    await hass.async_block_till_done()
    assert ems.mode == c.WITH_APPROVAL
    assert hass.states.get("select.ems_modus_akku").state == c.WITH_APPROVAL
    assert ems.state["log"][0]["text"] == f"Modus {c.PLAN_ONLY} → {c.WITH_APPROVAL}"


async def test_entities_of_the_executor(hass, ems):
    assert hass.states.get("select.ems_modus_akku").state == c.PLAN_ONLY
    assert hass.states.get("sensor.ems_akku_soll").state == c.NORMAL
    assert hass.states.get("sensor.ems_akku_soll").attributes["ist"] == c.NORMAL
    assert hass.states.get("binary_sensor.ems_akku_freigabe_offen").state == "off"
    assert float(hass.states.get("sensor.ems_ersparnis").state) == 0
    assert hass.states.get("number.ems_akku_verschleiss").state == "10.0"
    assert hass.states.get("number.ems_netzladeleistung").state == "5.0"
    assert len(hass.states.get("sensor.ems_lastprofil").attributes["profil"]) == 24
    assert hass.states.get("button.ems_akku_freigeben") is not None
    assert hass.states.get("sensor.ems_plan_akku") is not None


async def test_only_planning_never_switches(hass, ems, inverter, monkeypatch):
    with_plan(ems, monkeypatch, [block_now("h")])
    ems.evaluate()
    await ems.apply("pruefen")
    assert inverter["calls"] == []
    assert hass.states.get("sensor.ems_akku_soll").state == c.NORMAL


async def test_automatic_hold_switches_in_order_and_books(hass, ems, inverter, monkeypatch):
    with_plan(ems, monkeypatch, [block_now("h", spart=0.6, netz=2.0)])
    await ems.set_mode(c.AUTOMATIC)
    await hass.async_block_till_done()
    assert inverter["calls"] == [(DISCHARGE, 10), (FORCED, c.FORCED_STOP), (MODE, c.SELF_CONSUMPTION)]
    assert ems.state["set_by_ems"] == c.HOLD
    assert ems.state["running_block"]["action"] == "h" and ems.state["running_block"]["saves_eur"] == 0.6
    assert ems.state["log"][0]["art"] == "Geschaltet"
    assert hass.states.get("sensor.ems_akku_soll").attributes["ist"] == c.HOLD

    # the block ends: back to normal (discharge limit to the entity maximum), booking of the hold
    hass.states.async_set("sensor.total_imported_energy", "501.0")
    with_plan(ems, monkeypatch, [])
    inverter["calls"].clear()
    ems.evaluate()
    await hass.async_block_till_done()
    assert inverter["calls"] == [(FORCED, c.FORCED_STOP), (MODE, c.SELF_CONSUMPTION), (DISCHARGE, 5900)]
    savings = ems.state["savings"]
    assert savings["items"][0]["was"] == "h" and savings["items"][0]["kwh"] == 1.0
    assert savings["value"] == 0.3  # 0.6 € × 1 kWh measured / 2 kWh planned
    assert float(hass.states.get("sensor.ems_ersparnis").state) == 0.3


async def test_grid_charge_sets_power_and_forced_charge(hass, ems, inverter, monkeypatch):
    with_plan(ems, monkeypatch, [block_now("l", kwh=4.0, spart=0.8)])
    await ems.set_number("grid_charge_kw", 3.5)
    await ems.set_mode(c.AUTOMATIC)
    await hass.async_block_till_done()
    assert inverter["calls"][:3] == [(POWER, 3500), (FORCED, c.FORCED_CHARGE), (MODE, c.FORCED_MODE)]
    assert ems.state["set_by_ems"] == c.CHARGE


async def test_full_battery_holds_during_a_charge_block(hass, ems, inverter, monkeypatch):
    with_plan(ems, monkeypatch, [block_now("l", kwh=4.0)])
    hass.states.async_set("sensor.battery_level", "98")
    await ems.set_mode(c.AUTOMATIC)
    await hass.async_block_till_done()
    assert ems.target == c.HOLD
    assert inverter["calls"] == [(DISCHARGE, 10), (FORCED, c.FORCED_STOP), (MODE, c.SELF_CONSUMPTION)]


async def test_unconfirmed_command_is_logged_as_error(hass, ems, inverter, monkeypatch):
    monkeypatch.setattr("custom_components.ems.executor.CONFIRM_S", 0.05)  # instead of 60 s
    with_plan(ems, monkeypatch, [block_now("h")])
    inverter["sim"]["stuck"] = True
    await ems.set_mode(c.AUTOMATIC)
    await hass.async_block_till_done()
    assert ems.state["log"][0]["art"] == "Fehler"
    assert ems.state["set_by_ems"] == c.NORMAL


async def test_foreign_setting_is_not_overridden(hass, ems, inverter, monkeypatch, freezer):
    inverter["init"](mode="VPP")
    freezer.tick(timedelta(minutes=6))
    with_plan(ems, monkeypatch, [block_now("h")])
    await ems.set_mode(c.AUTOMATIC)
    await hass.async_block_till_done()
    assert inverter["calls"] == []
    assert ems.state["log"][0]["art"] == "Manuell"
    await ems.apply("pruefen")
    assert [e["art"] for e in ems.state["log"]].count("Manuell") == 1  # reported once


async def test_back_to_plan_only_takes_back_own_setting(hass, ems, inverter, monkeypatch):
    with_plan(ems, monkeypatch, [block_now("h")])
    await ems.set_mode(c.AUTOMATIC)
    await hass.async_block_till_done()
    inverter["calls"].clear()
    await ems.set_mode(c.PLAN_ONLY)
    await hass.async_block_till_done()
    assert (DISCHARGE, 5900) in inverter["calls"]
    assert ems.inverter() == c.NORMAL


async def test_approval_by_service_and_by_push(hass, ems, inverter, monkeypatch):
    with_plan(ems, monkeypatch, [block_now("h")])
    await ems.set_mode(c.WITH_APPROVAL)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.ems_akku_freigabe_offen").state == "on"
    assert ems.target == c.NORMAL

    await hass.services.async_call("ems", "freigeben", blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.ems_akku_freigabe_offen").state == "off"
    assert ems.target == c.HOLD and ems.state["set_by_ems"] == c.HOLD

    await hass.services.async_call("ems", "freigabe_zuruecknehmen", blocking=True)
    await hass.async_block_till_done()
    assert ems.target == c.NORMAL

    # a new request answered with "Ablehnen" from the push notification
    ems.state["approval"] = c.Approval().as_dict()
    ems.evaluate()
    hass.bus.async_fire("mobile_app_notification_action", {"action": "EMS_AKKU_NEIN"})
    await hass.async_block_till_done()
    assert ems.state["log"][0]["art"] == "Abgelehnt"
    assert hass.states.get("binary_sensor.ems_akku_freigabe_offen").state == "off"


async def test_request_is_pushed_after_five_minutes(hass, ems, monkeypatch, freezer):
    sent: list[dict] = []

    async def notify(call: ServiceCall) -> None:
        sent.append(dict(call.data))

    hass.services.async_register("notify", "phone_test", notify)
    ems.conf["benachrichtigung"] = "notify.phone_test"
    freezer.move_to(dt_util.now().replace(hour=19, minute=0))
    with_plan(ems, monkeypatch, [block_now("h", minutes_ago=-120)])
    await ems.set_mode(c.WITH_APPROVAL)
    freezer.tick(timedelta(minutes=6))
    await ems._maybe_request(dt_util.now())
    await hass.async_block_till_done()
    assert len(sent) == 1 and sent[0]["title"] == "🔋 Akku-Plan freigeben?"
    assert [a["action"] for a in sent[0]["data"]["actions"]] == ["EMS_AKKU_JA", "EMS_AKKU_NEIN"]
    await ems._maybe_request(dt_util.now())
    assert len(sent) == 1  # not again for the same pending request


async def test_settings_change_the_plan_inputs(hass, ems):
    # the simulated inverter replaces number.set_value: call the entity directly
    entity = hass.data["entity_components"]["number"].get_entity("number.ems_akku_verschleiss")
    await entity.async_set_native_value(12.5)
    await hass.async_block_till_done()
    assert ems.wear == 12.5


async def test_takeover_of_the_yaml_executor(hass, set_states, tmp_path, inverter):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "ems").mkdir()
    takeover = {
        "mode": c.WITH_APPROVAL,
        "approval": {"until_ms": 1, "codes": ["l"], "kwh": 10.5, "rejected_until_ms": 0},
        "set_by_ems": c.NORMAL,
        "log": [{"zeit": "2026-11-10T20:10:00+01:00", "art": "Abgerechnet", "text": "Korrektur"}],
        "savings": {"value": 1.021, "items": [{"was": "k", "betrag": 1.109}], "since": "2026-11-09"},
        "forecast": {"value": 3.5, "last_day": 1.5, "since": "2026-11-08"},
        "load_profile": {"values": [500] * 24},
        "wear_ct": 10,
        "grid_charge_kw": 5,
    }
    (tmp_path / "ems" / "uebernahme.json").write_text(json.dumps(takeover))
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "template", "ems_ersparnis", suggested_object_id="ems_ersparnis")
    registry.async_get_or_create(
        "sensor", "ems", "ems_neu_plan_akku", suggested_object_id="ems_plan_akku_neu"
    )
    set_states(**{"sensor.battery_level": "45.0"})
    conf = {
        k: v
        for k, v in ENTITIES.items()
        if k not in ("verschleiss", "netzladeleistung", "soll", "lastprofil")
    }
    assert await async_setup_component(
        hass, "ems", {"ems": {**conf, "aufzeichnung": False, "messung": False}}
    )
    await hass.async_block_till_done()

    assert hass.states.get("select.ems_modus_akku").state == c.WITH_APPROVAL
    assert float(hass.states.get("sensor.ems_ersparnis").state) == 1.021
    assert registry.async_get("sensor.ems_ersparnis").platform == "ems"  # same ID: statistics continue
    assert registry.async_get("sensor.ems_plan_akku").unique_id == "ems_neu_plan_akku"
    assert hass.states.get("sensor.ems_lastprofil").state == "500"
    log = hass.states.get("sensor.ems_protokoll").attributes["eintraege"]
    assert "übernommen" in log[0]["text"] and log[1]["text"] == "Korrektur"
    assert not (tmp_path / "ems" / "uebernahme.json").exists()
    assert (tmp_path / "ems" / "uebernahme.json.uebernommen").exists()


async def test_values_fixed_in_yaml_cannot_be_changed(hass, set_states, tmp_path, inverter):
    from homeassistant.exceptions import HomeAssistantError

    hass.config.config_dir = str(tmp_path)
    set_states(**{"sensor.battery_level": "45.0"})
    conf = {
        k: v
        for k, v in ENTITIES.items()
        if k not in ("verschleiss", "netzladeleistung", "soll", "lastprofil")
    }
    yaml = {**conf, "aufzeichnung": False, "messung": False, "verschleiss_ct": 10, "netzladeleistung_kw": 5}
    assert await async_setup_component(hass, "ems", {"ems": yaml})
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries("ems")
    ex = entry.runtime_data.executor
    state = hass.states.get("number.ems_akku_verschleiss")
    assert state.state == "10.0" and state.attributes["fest_in_yaml"] is True
    with pytest.raises(HomeAssistantError):
        await ex.set_number("wear_ct", 15)
    assert ex.wear == 10


async def test_passive_while_the_yaml_executor_is_loaded(hass, ems, inverter, monkeypatch):
    hass.states.async_set("automation.ems_akku_steuern", "on")
    with_plan(ems, monkeypatch, [block_now("h")])
    await ems.set_mode(c.AUTOMATIC)
    await hass.async_block_till_done()
    assert inverter["calls"] == []
    assert "YAML-Steuerung" in ems.state["log"][0]["text"]


async def test_no_switching_before_the_first_plan(hass, ems, inverter, monkeypatch):
    """During a restart a running hold stays until the first plan is there."""
    inverter["init"](discharge=10)  # hold is set
    ems.state["set_by_ems"] = c.HOLD
    ems.target = c.HOLD
    monkeypatch.setattr(ems.coordinator, "data", None)
    ems.state["mode"] = c.AUTOMATIC
    ems.evaluate()
    await ems.apply("start")
    await hass.async_block_till_done()
    assert ems.target == c.HOLD and inverter["calls"] == []
