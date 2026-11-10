"""Mode of the executor: Nur planen / Mit Freigabe / Automatisch."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import control as c
from .entity import ExecutorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([ModeSelect(entry.runtime_data.executor)])


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
