"""Setup dialog and options. configuration.yaml (`ems:`) keeps working: it is imported into the same
config entry on every start and then takes precedence over the options set in the dialog."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_APPROVAL,
    CONF_APPROVE,
    CONF_APPROVED_UNTIL,
    CONF_BATTERY_POWER,
    CONF_CAPACITY,
    CONF_DISCHARGE_MAX,
    CONF_EMS_MODE,
    CONF_FEED_IN_CT,
    CONF_FORCED,
    CONF_GRID_CHARGE_POWER,
    CONF_GRID_POWER,
    CONF_LOAD_POWER,
    CONF_LOAD_PROFILE,
    CONF_LOG,
    CONF_MEASUREMENT,
    CONF_MODE,
    CONF_PRICE_NOW,
    CONF_PRICES,
    CONF_PV,
    CONF_PV_POWER,
    CONF_RECORDING,
    CONF_REJECT,
    CONF_RESERVE,
    CONF_SAVINGS,
    CONF_SAVINGS_FORECAST,
    CONF_SOC,
    CONF_TARGET,
    CONF_WEAR,
    CONTROL_KEYS,
    DOMAIN,
    MEASUREMENT_KEYS,
    OPTION_DEFAULTS,
    PLANNER_KEYS,
    REQUIRED_KEYS,
)

TITLE = "Expensive Mini Savings"

DOMAINS = {
    CONF_PRICES: ["sensor"],
    CONF_PV: ["sensor"],
    CONF_LOAD_PROFILE: ["sensor"],
    CONF_SOC: ["sensor"],
    CONF_CAPACITY: ["sensor", "input_number", "number"],
    CONF_RESERVE: ["number", "input_number", "sensor"],
    CONF_WEAR: ["input_number", "number", "sensor"],
    CONF_GRID_CHARGE_POWER: ["input_number", "number", "sensor"],
    CONF_TARGET: ["sensor"],
    CONF_LOAD_POWER: ["sensor"],
    CONF_PV_POWER: ["sensor"],
    CONF_BATTERY_POWER: ["sensor"],
    CONF_GRID_POWER: ["sensor"],
    CONF_PRICE_NOW: ["sensor"],
    CONF_EMS_MODE: ["select", "input_select", "sensor"],
    CONF_FORCED: ["select", "input_select", "sensor"],
    CONF_DISCHARGE_MAX: ["number", "input_number", "sensor"],
    CONF_MODE: ["input_select", "select"],
    CONF_APPROVAL: ["binary_sensor"],
    CONF_APPROVED_UNTIL: ["input_datetime", "sensor"],
    CONF_APPROVE: ["script"],
    CONF_REJECT: ["script"],
    CONF_SAVINGS: ["sensor"],
    CONF_SAVINGS_FORECAST: ["sensor"],
    CONF_LOG: ["sensor"],
}


def merged_config(entry: ConfigEntry) -> dict[str, Any]:
    """Effective configuration: defaults ← entry data ← options. For an entry imported from
    configuration.yaml the YAML wins, options are ignored."""
    options = {} if entry.source == SOURCE_IMPORT else dict(entry.options)
    return {**OPTION_DEFAULTS, **entry.data, **options}


def format_settings(conf: dict[str, Any]) -> str:
    """Markdown list of the effective settings (keys as in configuration.yaml)."""
    keys = [
        *PLANNER_KEYS,
        CONF_FEED_IN_CT,
        CONF_RECORDING,
        CONF_MEASUREMENT,
        *MEASUREMENT_KEYS,
        *CONTROL_KEYS,
    ]
    return "\n".join(
        f"- `{key}`: {conf.get(key) if conf.get(key) not in (None, '') else '–'}" for key in keys
    )


def _entity(key: str) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=DOMAINS[key]))


def _planner_schema() -> vol.Schema:
    fields: dict[Any, Any] = {}
    for key in PLANNER_KEYS:
        marker = vol.Required if key in REQUIRED_KEYS else vol.Optional
        fields[marker(key)] = _entity(key)
    fields[vol.Required(CONF_FEED_IN_CT)] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=50, step=0.1, unit_of_measurement="ct/kWh", mode="box")
    )
    fields[vol.Required(CONF_RECORDING)] = selector.BooleanSelector()
    return vol.Schema(fields)


def _measurement_schema() -> vol.Schema:
    fields: dict[Any, Any] = {vol.Required(CONF_MEASUREMENT): selector.BooleanSelector()}
    for key in MEASUREMENT_KEYS:
        fields[vol.Optional(key)] = _entity(key)
    return vol.Schema(fields)


def _control_schema() -> vol.Schema:
    return vol.Schema({vol.Optional(key): _entity(key) for key in CONTROL_KEYS})


class EmsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_measurement()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_planner_schema(), OPTION_DEFAULTS),
        )

    async def async_step_measurement(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_control()
        return self.async_show_form(
            step_id="measurement",
            data_schema=self.add_suggested_values_to_schema(_measurement_schema(), OPTION_DEFAULTS),
        )

    async def async_step_control(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title=TITLE, data={**self._data, **user_input})
        return self.async_show_form(step_id="control", data_schema=_control_schema())

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """configuration.yaml → config entry. Runs on every start and updates the entry."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured(updates=dict(import_data))
        return self.async_create_entry(title=TITLE, data=dict(import_data))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EmsOptionsFlow:
        return EmsOptionsFlow()


class EmsOptionsFlow(OptionsFlow):
    """Change the inputs later (Settings → Devices & services → Expensive Mini Savings → Configure)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self.config_entry.source == SOURCE_IMPORT:
            # read-only: show the settings from configuration.yaml
            return self.async_abort(
                reason="yaml",
                description_placeholders={"settings": format_settings(merged_config(self.config_entry))},
            )
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_measurement()
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _planner_schema(), merged_config(self.config_entry)
            ),
        )

    async def async_step_measurement(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_control()
        return self.async_show_form(
            step_id="measurement",
            data_schema=self.add_suggested_values_to_schema(
                _measurement_schema(), merged_config(self.config_entry)
            ),
        )

    async def async_step_control(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self._data, **user_input})
        return self.async_show_form(
            step_id="control",
            data_schema=self.add_suggested_values_to_schema(
                _control_schema(), merged_config(self.config_entry)
            ),
        )
