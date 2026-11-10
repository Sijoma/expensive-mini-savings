"""The app's websocket commands: configuration and plan against reality."""

from __future__ import annotations

import json

from homeassistant.setup import async_setup_component
from test_integration import ENTITIES


async def setup(hass, set_states, tmp_path, conf=None):
    """Without a frozen clock: the websocket login token is issued at real time."""
    hass.config.config_dir = str(tmp_path)
    await hass.config.async_set_time_zone("Europe/Berlin")
    set_states(**{"sensor.battery_level": "45.0"})
    assert await async_setup_component(hass, "ems", {"ems": {**ENTITIES, **(conf or {})}})
    await hass.async_block_till_done()


async def test_config_lists_sensors_and_entities(hass, hass_ws_client, set_states, tmp_path):
    await setup(hass, set_states, tmp_path, {"aufzeichnung": False})
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "ems/config"})
    msg = await client.receive_json()
    assert msg["success"]
    r = msg["result"]
    assert r["sensoren"]["plan"] == "sensor.ems_plan_akku"
    assert r["sensoren"]["wert"] == "sensor.ems_wert_akku"
    # the executor's own entities
    assert r["entitaeten"]["modus"] == "select.ems_modus_akku"
    assert r["entitaeten"]["freigabe"] == "binary_sensor.ems_akku_freigabe_offen"
    assert r["entitaeten"]["ersparnis"] == "sensor.ems_ersparnis"
    assert r["dienste"] is True
    # a configured input takes precedence over the own entity
    assert r["entitaeten"]["verschleiss"] == "input_number.battery_wear"
    assert r["entitaeten"]["soc"] == "sensor.battery_level"
    assert "ablehnen" not in r["entitaeten"]
    assert r["einstellungen"]["aufzeichnung"] is False
    assert r["yaml"] is True
    assert r["version"] != "0"
    assert r["planer"]["sperren_min"] == 30 and 0.9 < r["planer"]["akku_haus"] <= 1


async def test_history_of_a_day(hass, hass_ws_client, set_states, tmp_path):
    await setup(hass, set_states, tmp_path)
    folder = tmp_path / "ems" / "messung"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "2026-11-07.jsonl").write_text(
        json.dumps({"start": "2026-11-07T10:00:00+01:00", "soc_prozent": 60, "aktion": "n"}) + "\n"
    )
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "ems/history", "tag": "2026-11-07"})
    msg = await client.receive_json()
    assert msg["success"]
    assert msg["result"]["viertelstunden"][0]["soc"] == 60
    assert "2026-11-07" in msg["result"]["tage"]

    await client.send_json_auto_id({"type": "ems/history", "tag": "07.11.2026"})
    msg = await client.receive_json()
    assert not msg["success"] and msg["error"]["code"] == "invalid_format"
