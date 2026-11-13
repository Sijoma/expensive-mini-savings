"""Settings of the planner: battery wear and grid charge power."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import GRID_KW_MAX, GRID_KW_MIN, SETTING_STEP, WEAR_CT_MAX, WEAR_CT_MIN
from .entity import ExecutorEntity
from .settings import GRID_CHARGE, WEAR


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ex = entry.runtime_data.executor
    async_add_entities([WearNumber(ex), GridPowerNumber(ex)])


class _Setting(ExecutorEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    key = ""

    @property
    def native_value(self) -> float:
        return self.executor.settings.get(self.key).value

    @property
    def extra_state_attributes(self) -> dict[str, bool | str]:
        setting = self.executor.settings.get(self.key)
        attributes: dict[str, bool | str] = {"fest_in_yaml": setting.fixed_in_yaml}
        if setting.external:
            attributes["extern"] = setting.external
        return attributes

    async def async_set_native_value(self, value: float) -> None:
        await self.executor.set_number(self.key, value)


class WearNumber(_Setting):
    """Cost per kWh taken out of the battery."""

    _attr_name = "EMS Akku-Verschleiß"
    _attr_unique_id = "ems_akku_verschleiss"
    entity_id = "number.ems_akku_verschleiss"
    _attr_icon = "mdi:battery-heart-variant"
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_native_min_value = WEAR_CT_MIN
    _attr_native_max_value = WEAR_CT_MAX
    _attr_native_step = SETTING_STEP
    key = WEAR


class GridPowerNumber(_Setting):
    """Power when charging from the grid."""

    _attr_name = "EMS Netz-Ladeleistung"
    _attr_unique_id = "ems_netzladeleistung"
    entity_id = "number.ems_netzladeleistung"
    _attr_icon = "mdi:battery-charging-high"
    _attr_native_unit_of_measurement = "kW"
    _attr_native_min_value = GRID_KW_MIN
    _attr_native_max_value = GRID_KW_MAX
    _attr_native_step = SETTING_STEP
    key = GRID_CHARGE
