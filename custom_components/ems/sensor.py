"""Sensors: battery plan, value of one kWh in the battery, heat pump plan (planner); target, savings,
forecast, log and load profile (executor)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import control as c
from .coordinator import EmsCoordinator
from .core.explanation import NAMES
from .entity import ExecutorEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: EmsCoordinator = entry.runtime_data
    executor = coordinator.executor
    async_add_entities(
        [
            PlanSensor(coordinator),
            ValueSensor(coordinator),
            HeatPumpSensor(coordinator),
            TargetSensor(executor),
            SavingsSensor(executor),
            ForecastSensor(executor),
            LogSensor(executor),
            LoadProfileSensor(executor),
        ]
    )


class _EmsSensor(CoordinatorEntity[EmsCoordinator], SensorEntity):
    """Common base. No device on purpose: entity IDs stay sensor.ems_* for new installations too."""


class PlanSensor(_EmsSensor):
    _attr_name = "EMS Plan Akku"
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

    _attr_name = "EMS Plan Wärmepumpe"
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


class TargetSensor(ExecutorEntity, SensorEntity):
    """What should run at the battery now (former sensor.ems_akku_soll)."""

    _attr_name = "EMS Akku Soll"
    _attr_unique_id = "ems_akku_soll"
    entity_id = "sensor.ems_akku_soll"
    _attr_icon = "mdi:battery-sync"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.executor.target_entity_id = self.entity_id

    @property
    def native_value(self) -> str:
        return self.executor.target

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ex = self.executor
        return {
            "plan_aktuell": ex.plan_fresh(dt_util.now()),
            "ist": ex.inverter(),
            "gesetzt": ex.state["set_by_ems"],
        }


class SavingsSensor(ExecutorEntity, SensorEntity):
    """Measured savings of the executed blocks (EUR, total)."""

    _attr_name = "EMS Ersparnis"
    _attr_unique_id = "ems_ersparnis"
    entity_id = "sensor.ems_ersparnis"
    _attr_icon = "mdi:piggy-bank"
    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2
    _unrecorded_attributes = frozenset({"aktionen"})

    @property
    def native_value(self) -> float:
        return round(float(self.executor.state["savings"]["value"]), 3)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.executor.state["savings"]
        return {"aktionen": s["items"], "seit": s["since"]}


class ForecastSensor(ExecutorEntity, SensorEntity):
    """Trial run: the plan's expected savings of the next 24 h, added daily at 14:05 (EUR, total)."""

    _attr_name = "EMS Prognose-Ersparnis"
    _attr_unique_id = "ems_prognose_ersparnis"
    entity_id = "sensor.ems_prognose_ersparnis"
    _attr_icon = "mdi:cash-check"
    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float:
        return round(float(self.executor.state["forecast"]["value"]), 3)

    @property
    def last_reset(self) -> datetime | None:
        """Start of the current sum: a reset starts a new cycle instead of a jump in the statistics."""
        since = dt_util.parse_date(self.executor.state["forecast"].get("since") or "")
        return dt_util.start_of_local_day(since) if since else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        s = self.executor.state["forecast"]
        days = [{"zeit": d["at"], "wert": d["value"]} for d in s.get("days", [])]
        return {"letzter_tag": s["last_day"], "seit": s["since"], "tage": days}


class LogSensor(ExecutorEntity, SensorEntity):
    """The executor's decisions, newest first (the last 50)."""

    _attr_name = "EMS Protokoll"
    _attr_unique_id = "ems_protokoll"
    entity_id = "sensor.ems_protokoll"
    _attr_icon = "mdi:clipboard-text-clock"
    _unrecorded_attributes = frozenset({"eintraege"})

    @property
    def native_value(self) -> str | None:
        log = self.executor.state["log"]
        return log[0]["art"] if log else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"eintraege": self.executor.state["log"]}


class LoadProfileSensor(ExecutorEntity, SensorEntity):
    """Average house consumption per hour of day (W), learned hourly from the energy meter (15 % new)."""

    _attr_name = "EMS Lastprofil"
    _attr_unique_id = "ems_lastprofil"
    entity_id = "sensor.ems_lastprofil"
    _attr_icon = "mdi:home-lightning-bolt-outline"
    _attr_native_unit_of_measurement = "W"

    @property
    def native_value(self) -> int:
        profile = self.executor.profile or c.START_PROFILE
        return round(sum(profile) / len(profile))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"profil": self.executor.profile}
