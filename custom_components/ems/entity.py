"""Base class for the executor's entities: they update when the executor signals a change."""

from __future__ import annotations

from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import SIGNAL_EXECUTOR
from .executor import Executor


class ExecutorEntity(Entity):
    """No device on purpose: entity IDs stay <domain>.ems_* (the same IDs the former YAML executor had)."""

    _attr_should_poll = False

    def __init__(self, executor: Executor) -> None:
        self.executor = executor

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_EXECUTOR, self.async_write_ha_state))
