"""Setup from the dialog (Settings → Devices & services) or from configuration.yaml:

    ems:
      einspeisung_ct: 7      # feed-in compensation, optional
      aufzeichnung: true     # write inputs hourly to /config/ems/aufzeichnung/
      messung: true          # write measurements per quarter hour to /config/ems/messung/

Input entities: keys see const.ENTITY_KEYS, defaults const.DEFAULTS.
YAML is imported into the config entry on every start and takes precedence over options set in the dialog.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .config_flow import merged_config
from .const import (
    CONF_FEED_IN_CT,
    CONF_MEASUREMENT,
    CONF_PRICES,
    CONF_RECORDING,
    CONF_WEAR,
    DEFAULTS,
    DOMAIN,
    ENTITY_KEYS,
)
from .coordinator import EmsCoordinator
from .panel import async_register_panel, async_unregister_panel
from .recorder import MeasurementRecorder

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                **{
                    vol.Optional(k, default=DEFAULTS[k]) if k in DEFAULTS else vol.Optional(k): cv.entity_id
                    for k in ENTITY_KEYS
                },
                vol.Optional(CONF_FEED_IN_CT, default=7.0): vol.Coerce(float),
                vol.Optional(CONF_RECORDING, default=True): cv.boolean,
                vol.Optional(CONF_MEASUREMENT, default=True): cv.boolean,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """configuration.yaml → config entry (import). The entry is then set up like one from the dialog."""
    if DOMAIN in config:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=config[DOMAIN]
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    conf = merged_config(entry)
    coordinator = EmsCoordinator(hass, conf, entry)
    entry.runtime_data = coordinator

    @callback
    def replan(*_: object) -> None:
        hass.async_create_task(coordinator.async_request_refresh())

    # every 15 min shortly after the quarter hour starts, on new prices and on changed settings
    entry.async_on_unload(async_track_time_change(hass, replan, minute=[0, 15, 30, 45], second=10))

    @callback
    def input_changed(event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or old.state != new.state or old.attributes != new.attributes:
            replan()

    watched = [e for e in (conf.get(CONF_PRICES), conf.get(CONF_WEAR)) if e]
    entry.async_on_unload(async_track_state_change_event(hass, watched, input_changed))

    if conf.get(CONF_MEASUREMENT):
        measurement = MeasurementRecorder(hass, conf)
        measurement.begin()
        entry.async_on_unload(measurement.stop)

    if hass.is_running:
        await coordinator.async_refresh()
    else:
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, replan))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_panel(hass)
    entry.async_on_unload(entry.add_update_listener(_reload_on_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    async_unregister_panel(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _reload_on_change(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options or YAML changed: set the entry up again with the new configuration."""
    await hass.config_entries.async_reload(entry.entry_id)
