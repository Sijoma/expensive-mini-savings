"""Sensors: battery plan, value of one kWh in the battery, heat pump plan."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import EmsCoordinator
from .core.explanation import NAMES


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EmsCoordinator = entry.runtime_data
    async_add_entities([PlanSensor(coordinator), ValueSensor(coordinator), HeatPumpSensor(coordinator)])


class _EmsSensor(CoordinatorEntity[EmsCoordinator], SensorEntity):
    """Common base. No device on purpose: entity IDs stay sensor.ems_* for new installations too."""


class PlanSensor(_EmsSensor):
    _attr_name = "EMS Plan Akku neu"
    _attr_unique_id = "ems_neu_plan_akku"
    _attr_icon = "mdi:battery-sync"
    # keep the quarter-hour table (~10 KB) out of the database; the recorder files keep it
    _unrecorded_attributes = frozenset({"slots"})

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self) -> str | None:
        return NAMES[self.coordinator.data.plan.now] if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return self.coordinator.data.attribute if self.coordinator.data else None


class ValueSensor(_EmsSensor):
    """What one kWh in the battery is currently worth: the basis of every decision."""

    _attr_name = "EMS Wert Akku"
    _attr_unique_id = "ems_neu_wert_akku"
    _attr_icon = "mdi:battery-heart-variant"
    _attr_native_unit_of_measurement = "ct/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.attribute["wert_jetzt_ct"] if self.coordinator.data else None


class HeatPumpSensor(_EmsSensor):
    """Heat pump PV boost (planning only; switching is done by a separate SG Ready automation)."""

    _attr_name = "EMS Plan Wärmepumpe neu"
    _attr_unique_id = "ems_neu_plan_waermepumpe"
    _attr_icon = "mdi:heat-pump-outline"

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
        return "PV-Boost" if self.coordinator.data.plan.boost_now else "Normal"

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self.coordinator.data:
            return None
        return {"aktionen": self.coordinator.data.attribute["wp_aktionen"]}
