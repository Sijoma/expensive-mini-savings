"""Approve or reject what the plan wants to do in the next 24 h (also as services and push buttons)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ExecutorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    ex = entry.runtime_data.executor
    async_add_entities([ApproveButton(ex), RejectButton(ex)])


class ApproveButton(ExecutorEntity, ButtonEntity):
    _attr_name = "EMS Akku-Plan freigeben"
    _attr_unique_id = "ems_akku_freigeben"
    entity_id = "button.ems_akku_freigeben"
    _attr_icon = "mdi:check-circle"

    async def async_press(self) -> None:
        await self.executor.approve()


class RejectButton(ExecutorEntity, ButtonEntity):
    _attr_name = "EMS Akku-Plan ablehnen"
    _attr_unique_id = "ems_akku_ablehnen"
    entity_id = "button.ems_akku_ablehnen"
    _attr_icon = "mdi:close-circle"

    async def async_press(self) -> None:
        await self.executor.reject()
