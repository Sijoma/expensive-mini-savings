"""Setup from the dialog (Settings → Devices & services) or from configuration.yaml:

    ems:
      einspeisung_ct: 7      # feed-in compensation, optional
      aufzeichnung: true     # write inputs hourly to /config/ems/aufzeichnung/
      messung: true          # write measurements per quarter hour to /config/ems/messung/

Input entities: keys see const.ENTITY_KEYS, defaults const.DEFAULTS.
YAML is imported into the config entry on every start and takes precedence over options set in the dialog.
"""

from __future__ import annotations

from datetime import datetime

import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .config_flow import merged_config
from .const import (
    CONF_FEED_IN_CT,
    CONF_GRID_CHARGE_POWER,
    CONF_GRID_KW,
    CONF_MEASUREMENT,
    CONF_NOTIFY,
    CONF_PRICES,
    CONF_RECORDING,
    CONF_TARGET,
    CONF_WEAR,
    CONF_WEAR_CT,
    DEFAULTS,
    DOMAIN,
    ENTITY_KEYS,
    GRID_KW_MAX,
    GRID_KW_MIN,
    LEGACY_CONTROL_KEYS,
    WEAR_CT_MAX,
    WEAR_CT_MIN,
    external_input,
)
from .coordinator import EmsCoordinator
from .executor import Executor
from .inputs import Inputs
from .panel import async_register_panel, async_unregister_panel
from .prices import PriceSource
from .recorder import MeasurementRecorder
from .runtime import EmsRuntime
from .settings import PlannerSettings

PLATFORMS = [Platform.SENSOR, Platform.SELECT, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.BUTTON]

OWN_ENTITY_IDS = {
    "sensor.ems_akku_soll",
    "sensor.ems_ersparnis",
    "sensor.ems_prognose_ersparnis",
    "sensor.ems_protokoll",
    "sensor.ems_lastprofil",
    "binary_sensor.ems_akku_freigabe_offen",
}
"""Entity IDs the integration takes over from the former YAML executor (same IDs: statistics continue)."""
RENAMED = {
    ("sensor", "ems_neu_plan_akku"): "sensor.ems_plan_akku",
    ("sensor", "ems_neu_plan_waermepumpe"): "sensor.ems_plan_warmepumpe",
}
"""Plan sensors lose the '_neu' of the test phase."""

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
                vol.Optional(CONF_NOTIFY): cv.string,
                vol.Optional(CONF_WEAR_CT): vol.All(
                    vol.Coerce(float), vol.Range(min=WEAR_CT_MIN, max=WEAR_CT_MAX)
                ),
                vol.Optional(CONF_GRID_KW): vol.All(
                    vol.Coerce(float), vol.Range(min=GRID_KW_MIN, max=GRID_KW_MAX)
                ),
                **{vol.Optional(k): cv.entity_id for k in LEGACY_CONTROL_KEYS},
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


@callback
def _migrate_entities(hass: HomeAssistant) -> None:
    """Free the entity IDs of the former YAML executor (template entities no longer defined) and drop
    the '_neu' of the plan sensors."""
    registry = er.async_get(hass)
    for entity_id in OWN_ENTITY_IDS:
        entry = registry.async_get(entity_id)
        if entry is None or entry.platform == DOMAIN or entry.platform != "template":
            continue
        state = hass.states.get(entity_id)
        if state is None or state.attributes.get("restored"):
            registry.async_remove(entity_id)
    for (domain, unique_id), new_id in RENAMED.items():
        old_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        if old_id and old_id != new_id and registry.async_get(new_id) is None:
            registry.async_update_entity(old_id, new_entity_id=new_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    conf = merged_config(entry)
    _migrate_entities(hass)
    inputs = Inputs(hass, conf)
    settings = PlannerSettings(conf, inputs)
    prices = PriceSource(hass, conf, inputs)

    def running() -> tuple[str | None, datetime | None]:
        """What runs at the battery: an external `soll` entity if configured, else the executor's target."""
        if external_input(conf, CONF_TARGET):
            st = inputs.state(CONF_TARGET)
            return (st.state, st.last_changed) if st else (None, None)
        return executor.target, executor.target_since

    coordinator = EmsCoordinator(hass, conf, entry, inputs, settings, prices, running)
    executor = Executor(hass, conf, coordinator, inputs, settings, prices.now_ct)
    await executor.async_load()  # binds the stored settings before the first plan
    entry.runtime_data = EmsRuntime(conf, coordinator, executor, settings)
    _register_services(hass)

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

    watched = [e for e in (conf.get(CONF_PRICES), conf.get(CONF_WEAR), conf.get(CONF_GRID_CHARGE_POWER)) if e]
    entry.async_on_unload(async_track_state_change_event(hass, watched, input_changed))

    @callback
    def start_executor(*_: object) -> None:
        executor.start()

    entry.async_on_unload(executor.stop)
    if hass.is_running:
        await coordinator.async_refresh()
        start_executor()
    else:
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, replan))
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, start_executor))

    if conf.get(CONF_MEASUREMENT):
        # after the first plan when HA is already running (reload), so the first quarter hour has a price;
        # on an HA start the first plan only comes at EVENT_HOMEASSISTANT_STARTED (then no price with Tibber)
        measurement = MeasurementRecorder(hass, conf, inputs, prices.now_ct)
        measurement.begin()
        entry.async_on_unload(measurement.stop)

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


SERVICES = ("freigeben", "ablehnen", "freigabe_zuruecknehmen", "prognose_zuruecksetzen")


@callback
def _register_services(hass: HomeAssistant) -> None:
    """ems.freigeben / ems.ablehnen / ems.freigabe_zuruecknehmen act on the loaded entry."""
    if hass.services.has_service(DOMAIN, SERVICES[0]):
        return

    def executor() -> Executor | None:
        entries = [e for e in hass.config_entries.async_loaded_entries(DOMAIN)]
        return entries[0].runtime_data.executor if entries else None

    async def approve(_call: ServiceCall) -> None:
        if ex := executor():
            await ex.approve()

    async def reject(_call: ServiceCall) -> None:
        if ex := executor():
            await ex.reject()

    async def withdraw(_call: ServiceCall) -> None:
        if ex := executor():
            await ex.withdraw()

    hass.services.async_register(DOMAIN, "freigeben", approve)
    hass.services.async_register(DOMAIN, "ablehnen", reject)
    hass.services.async_register(DOMAIN, "freigabe_zuruecknehmen", withdraw)

    async def reset_forecast(_call: ServiceCall) -> None:
        if ex := executor():
            await ex.reset_forecast()

    hass.services.async_register(DOMAIN, "prognose_zuruecksetzen", reset_forecast)
