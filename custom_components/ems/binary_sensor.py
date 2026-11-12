"""Approval pending: in mode 'Mit Freigabe', something in the plan waits for approval."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import control as c
from .entity import ExecutorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([ApprovalPending(entry.runtime_data.executor)])


class ApprovalPending(ExecutorEntity, BinarySensorEntity):
    _attr_name = "EMS Akku Freigabe offen"
    _attr_unique_id = "ems_akku_freigabe_offen"
    entity_id = "binary_sensor.ems_akku_freigabe_offen"
    _attr_icon = "mdi:account-question"

    @property
    def is_on(self) -> bool:
        return self.executor.is_pending

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        a = self.executor.approval
        until = dt_util.utc_from_timestamp(a.until_ms / 1000) if a.until_ms else None
        offered = self.executor.offered()
        would_hold = c.approval_end(offered, dt_util.now()) if offered else None
        return {
            # all runnable blocks of the next 24 h: this is what gets approved
            "aktionen": offered,
            # how long an approval given now would hold
            "gilt_bis_bei_freigabe": (
                dt_util.as_local(dt_util.utc_from_timestamp(would_hold / 1000)).isoformat()
                if would_hold
                else None
            ),
            "freigegeben_bis": dt_util.as_local(until).isoformat() if until else None,
            "freigegeben": sorted(a.codes),
            "freigegeben_kwh": a.kwh,
        }
