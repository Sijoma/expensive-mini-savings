"""Modes of the executor: battery (Nur planen / Mit Freigabe / Automatisch), heat pump (Nur planen /
Automatisch)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import control as c
from . import heat_pump_control as hp
from .entity import ExecutorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    executor = entry.runtime_data.executor
    async_add_entities(
        [ModeSelect(executor), *([HeatPumpModeSelect(executor)] if executor.heat_pump else [])]
    )


class ModeSelect(ExecutorEntity, SelectEntity):
    _attr_name = "EMS Modus Akku"
    _attr_unique_id = "ems_modus_akku"
    entity_id = "select.ems_modus_akku"
    _attr_icon = "mdi:battery-lock"

    def __init__(self, executor) -> None:
        super().__init__(executor)
        self._attr_options = list(c.MODES)

    @property
    def current_option(self) -> str:
        return self.executor.mode

    async def async_select_option(self, option: str) -> None:
        await self.executor.set_mode(option)


class HeatPumpModeSelect(ExecutorEntity, SelectEntity):
    _attr_name = "EMS Modus Wärmepumpe"
    _attr_unique_id = "ems_modus_waermepumpe"
    entity_id = "select.ems_modus_waermepumpe"
    _attr_icon = "mdi:heat-pump-outline"

    def __init__(self, executor) -> None:
        super().__init__(executor)
        self._attr_options = list(hp.MODES)

    @property
    def current_option(self) -> str:
        return self.executor.heat_pump.mode

    async def async_select_option(self, option: str) -> None:
        await self.executor.heat_pump.set_mode(option)
