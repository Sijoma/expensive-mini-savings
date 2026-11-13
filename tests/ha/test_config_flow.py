"""Setup dialog, options and the YAML import."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

PLAN = "sensor.ems_plan_akku"


async def _prepare(hass, freezer, set_states, now, tmp_path):
    hass.config.config_dir = str(tmp_path)
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(now)
    set_states(**{"sensor.battery_level": "45.0"})


async def test_dialog_creates_entry_with_defaults(hass, freezer, set_states, now, tmp_path):
    await _prepare(hass, freezer, set_states, now, tmp_path)
    result = await hass.config_entries.flow.async_init("ems", context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "preise": "sensor.electricity_prices",
            "soc": "sensor.battery_level",
            "einspeisung_ct": 8.0,
            "aufzeichnung": False,
        },
    )
    assert result["step_id"] == "measurement"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"messung": False})
    assert result["step_id"] == "control"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"benachrichtigung": "notify.phone_test"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries("ems")[0]
    assert entry.data["einspeisung_ct"] == 8.0
    assert hass.states.get(PLAN) is not None
    assert entry.runtime_data.conf["einspeisung_ct"] == 8.0
    assert entry.data["benachrichtigung"] == "notify.phone_test"


async def test_only_one_entry(hass):
    MockConfigEntry(domain="ems", unique_id="ems", data={}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init("ems", context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT


async def test_yaml_is_imported_and_wins(hass, freezer, set_states, now, tmp_path):
    await _prepare(hass, freezer, set_states, now, tmp_path)
    assert await async_setup_component(hass, "ems", {"ems": {"einspeisung_ct": 9, "aufzeichnung": False}})
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries("ems")
    assert entry.source == config_entries.SOURCE_IMPORT
    assert entry.runtime_data.conf["einspeisung_ct"] == 9
    assert hass.states.get(PLAN) is not None
    # options cannot override YAML
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "yaml"
    settings = result["description_placeholders"]["settings"]
    assert "`einspeisung_ct`: 9" in settings and "`soc`: sensor.battery_level" in settings
    assert "`preise`: Tibber (tibber.get_prices)" in settings


async def test_yaml_change_updates_the_entry(hass, freezer, set_states, now, tmp_path):
    await _prepare(hass, freezer, set_states, now, tmp_path)
    MockConfigEntry(
        domain="ems", unique_id="ems", source=config_entries.SOURCE_IMPORT, data={"einspeisung_ct": 7.0}
    ).add_to_hass(hass)
    assert await async_setup_component(hass, "ems", {"ems": {"einspeisung_ct": 6.5, "aufzeichnung": False}})
    await hass.async_block_till_done()
    (entry,) = hass.config_entries.async_entries("ems")
    assert entry.data["einspeisung_ct"] == 6.5


async def test_options_change_reloads(hass, freezer, set_states, now, tmp_path):
    await _prepare(hass, freezer, set_states, now, tmp_path)
    entry = MockConfigEntry(
        domain="ems", unique_id="ems", data={"einspeisung_ct": 7.0, "aufzeichnung": False, "messung": False}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "preise": "sensor.electricity_prices",
            "soc": "sensor.battery_level",
            "einspeisung_ct": 12.0,
            "aufzeichnung": False,
        },
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"messung": False})
    assert result["step_id"] == "control"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.conf["einspeisung_ct"] == 12.0
    assert hass.states.get(PLAN) is not None


async def test_unload_removes_timers(hass, freezer, set_states, now, tmp_path):
    await _prepare(hass, freezer, set_states, now, tmp_path)
    entry = MockConfigEntry(domain="ems", unique_id="ems", data={"aufzeichnung": False, "messung": True})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PLAN).state == "unavailable"
