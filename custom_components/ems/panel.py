"""The EMS app: a sidebar panel served from this integration, plus websocket commands for its data.

The panel itself (frontend/ems-panel.js) is built from ../../frontend (TypeScript, Lit) and shipped
with the integration, so it needs no separate frontend install."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import history
from .adapter import LOAD_FACTOR, P10_SHARE
from .config_flow import merged_config
from .const import DOMAIN, ENTITY_KEYS
from .core.forecast import SPIKE_SHARE
from .core.model import MIN_HOLD_SLOTS, Battery
from .core.optimizer import SWITCH_CT

_LOGGER = logging.getLogger(__name__)

PANEL_URL = "ems"
STATIC_URL = "/ems_static"
FRONTEND_DIR = Path(__file__).parent / "frontend"
MODULE = "ems-panel.js"
DATA_STATIC = f"{DOMAIN}_static_registered"
DATA_WS = f"{DOMAIN}_ws_registered"
DATA_VERSION = f"{DOMAIN}_version"

SENSORS = {
    "plan": "ems_neu_plan_akku",
    "wert": "ems_neu_wert_akku",
    "waermepumpe": "ems_neu_plan_waermepumpe",
}
"""App name → unique_id of our sensors (entity IDs can be renamed by the user)."""


def _version() -> str:
    try:
        return json.loads((Path(__file__).parent / "manifest.json").read_text())["version"]
    except (OSError, KeyError, ValueError):
        return "0"


async def async_register_panel(hass: HomeAssistant) -> None:
    hass.data[DATA_VERSION] = version = await hass.async_add_executor_job(_version)
    if not hass.data.get(DATA_WS):
        websocket_api.async_register_command(hass, ws_config)
        websocket_api.async_register_command(hass, ws_history)
        hass.data[DATA_WS] = True

    if not (FRONTEND_DIR / MODULE).is_file():
        _LOGGER.warning("EMS app not built (%s missing), sidebar panel not available", MODULE)
        return
    if hass.http is None:  # no web server (tests without http)
        return
    if not hass.data.get(DATA_STATIC):
        # static paths cannot be removed again, so register them once per HA run
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), True)])
        hass.data[DATA_STATIC] = True

    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="ems-panel",
        sidebar_title="EMS",
        sidebar_icon="mdi:battery-clock-outline",
        module_url=f"{STATIC_URL}/{MODULE}?v={version}",  # new version → browser loads the new file
        require_admin=False,
    )


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)


def planner_facts() -> dict[str, float]:
    """The assumptions the planner calculates with, for the app's explanation (not the author's house)."""
    b = Battery()
    return {
        "pv_akku": b.efficiency_charge,
        "netz_akku": b.efficiency_grid_charge,
        "akku_haus": b.efficiency_discharge,
        "standby_w": round(b.standby_kw * 1000),
        "netzladen_max_kw": b.grid_charge_kw,
        "wechsel_ct": SWITCH_CT,
        "sperren_min": MIN_HOLD_SLOTS * 15,
        "last_faktor": LOAD_FACTOR,
        "pv_p10_anteil": P10_SHARE,
        "spitzen_anteil": SPIKE_SHARE,
    }


def app_config(hass: HomeAssistant) -> dict[str, Any]:
    """What the app needs to know: our sensors and the configured input and executor entities."""
    entries = hass.config_entries.async_entries(DOMAIN)
    entry = entries[0] if entries else None
    conf = merged_config(entry) if entry else {}
    registry = er.async_get(hass)
    sensors = {
        name: registry.async_get_entity_id("sensor", DOMAIN, unique_id) for name, unique_id in SENSORS.items()
    }
    return {
        "version": hass.data.get(DATA_VERSION, "0"),
        "sensoren": sensors,
        "entitaeten": {key: conf.get(key) for key in ENTITY_KEYS if conf.get(key)},
        "einstellungen": {k: v for k, v in conf.items() if k not in ENTITY_KEYS},
        "yaml": bool(entry and entry.source == SOURCE_IMPORT),
        "eintrag": entry.entry_id if entry else None,
        "planer": planner_facts(),
    }


@websocket_api.websocket_command({vol.Required("type"): "ems/config"})
@callback
def ws_config(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    connection.send_result(msg["id"], app_config(hass))


@websocket_api.websocket_command({vol.Required("type"): "ems/history", vol.Optional("tag"): str})
@websocket_api.async_response
async def ws_history(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    try:
        day_ = date.fromisoformat(msg["tag"]) if msg.get("tag") else dt_util.now().date()
    except ValueError:
        connection.send_error(msg["id"], "invalid_format", "tag must be YYYY-MM-DD")
        return
    config_dir = Path(hass.config.config_dir)

    def load() -> dict[str, Any]:
        result = history.day(config_dir, day_)
        result["tage"] = history.available_days(config_dir)
        return result

    connection.send_result(msg["id"], await hass.async_add_executor_job(load))
