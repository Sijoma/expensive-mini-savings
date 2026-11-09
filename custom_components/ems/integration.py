"""Setup from configuration.yaml:

    ems:
      einspeisung_ct: 7      # feed-in compensation, optional
      aufzeichnung: true     # write inputs hourly to /config/ems/aufzeichnung/
      messung: true          # write measurements per quarter hour to /config/ems/messung/

Input entities can be overridden (keys see const.DEFAULTS).
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_FEED_IN_CT,
    CONF_MEASUREMENT,
    CONF_PRICES,
    CONF_RECORDING,
    CONF_WEAR,
    DEFAULTS,
    DOMAIN,
)
from .coordinator import EmsCoordinator
from .recorder import MeasurementRecorder

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                **{vol.Optional(k, default=v): cv.entity_id for k, v in DEFAULTS.items()},
                vol.Optional(CONF_FEED_IN_CT, default=7.0): vol.Coerce(float),
                vol.Optional(CONF_RECORDING, default=True): cv.boolean,
                vol.Optional(CONF_MEASUREMENT, default=True): cv.boolean,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    conf = config.get(DOMAIN)
    if conf is None:
        return True
    coordinator = EmsCoordinator(hass, conf)
    hass.data[DOMAIN] = coordinator

    @callback
    def replan(*_: object) -> None:
        hass.async_create_task(coordinator.async_request_refresh())

    # every 15 min shortly after the quarter hour starts, on new prices and on changed settings
    unsubscribe = [async_track_time_change(hass, replan, minute=[0, 15, 30, 45], second=10)]

    @callback
    def input_changed(event: Event) -> None:
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or old.state != new.state or old.attributes != new.attributes:
            replan()

    unsubscribe.append(
        async_track_state_change_event(hass, [conf[CONF_PRICES], conf[CONF_WEAR]], input_changed)
    )

    if conf[CONF_MEASUREMENT]:
        measurement = MeasurementRecorder(hass, conf)
        measurement.begin()
        unsubscribe.append(measurement.stop)

    @callback
    def stop(_: Event) -> None:
        for f in unsubscribe:
            f()

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)

    if hass.is_running:
        await coordinator.async_refresh()
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, replan)

    hass.async_create_task(async_load_platform(hass, "sensor", DOMAIN, {}, config))
    return True
