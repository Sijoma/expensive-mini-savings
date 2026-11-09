"""Reads the inputs, plans in a worker thread and holds the result for the sensors."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .adapter import battery_from, build_slots, number, plan_attributes, running_action
from .const import (
    CONF_CAPACITY,
    CONF_FEED_IN_CT,
    CONF_GRID_CHARGE_POWER,
    CONF_LOAD_PROFILE,
    CONF_PRICES,
    CONF_PV,
    CONF_RECORDING,
    CONF_RESERVE,
    CONF_SOC,
    CONF_TARGET,
    CONF_WEAR,
)
from .core.explanation import Plan, create_plan
from .core.model import Tariff

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Result:
    plan: Plan
    attribute: dict[str, Any]


class EmsCoordinator(DataUpdateCoordinator[Result]):
    def __init__(self, hass: HomeAssistant, conf: dict[str, Any], entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name="EMS", update_interval=None)
        self.conf = conf
        self._last_recording: str | None = None

    def _state(self, key: str) -> Any:
        entity_id = self.conf.get(key)
        return self.hass.states.get(entity_id) if entity_id else None

    def _attribute(self, key: str, attribute_name: str) -> Any:
        z = self._state(key)
        return z.attributes.get(attribute_name) if z else None

    def _value(self, key: str) -> Any:
        z = self._state(key)
        return z.state if z else None

    async def _async_update_data(self) -> Result:
        now = dt_util.now()
        slots = build_slots(
            self._attribute(CONF_PRICES, "preise"),
            self._attribute(CONF_PV, "forecast"),
            self._attribute(CONF_LOAD_PROFILE, "profil"),
            now,
        )
        if not slots:
            raise UpdateFailed("no prices from now on")
        battery = battery_from(
            self._value(CONF_CAPACITY),
            self._value(CONF_RESERVE),
            self._value(CONF_WEAR),
            self._value(CONF_GRID_CHARGE_POWER),
        )
        soc = number(self._value(CONF_SOC), float("nan"))
        if soc != soc:  # nan: SoC unknown
            raise UpdateFailed("battery state of charge unknown")
        e0 = soc / 100 * battery.capacity_kwh
        target = self._state(CONF_TARGET)
        running, since = running_action(
            target.state if target else None, target.last_changed if target else None, now
        )
        tariff = Tariff(feed_in_ct=float(self.conf.get(CONF_FEED_IN_CT, 7.0)))

        t0 = time.perf_counter()
        plan = await self.hass.async_add_executor_job(
            lambda: create_plan(slots, e0, battery, tariff, running=running, since=since)
        )
        runtime = (time.perf_counter() - t0) * 1000
        attribute = plan_attributes(plan, battery, now, runtime)

        if self.conf.get(CONF_RECORDING):
            await self._record(now, soc, running, since, plan, attribute)
        return Result(plan, attribute)

    async def _record(
        self, now: datetime, soc: float, running: Any, since: int, plan: Plan, attribute: dict[str, Any]
    ) -> None:
        """Store inputs and plan once per hour (for later backtests with real forecasts).
        One JSON line per run, one file per day."""
        hour_key = now.strftime("%Y-%m-%dT%H")
        if hour_key == self._last_recording:
            return
        self._last_recording = hour_key
        line = {
            "zeit": now.isoformat(timespec="seconds"),
            "soc": soc,
            "laeuft": running.value if running else None,
            "seit": since,
            "slots": [
                [r[0], r[1], r[2], r[3], round(s.spike_kw, 3), r[4]]
                for r, s in zip(attribute["slots"], plan.slots, strict=True)
            ],
            "aktionen": "".join(a.value for a in plan.actions),
            "ersparnis_eur": attribute["ersparnis_eur"],
            "rechenzeit_ms": attribute["rechenzeit_ms"],
        }
        file_path = Path(self.hass.config.path("ems", "aufzeichnung", f"{now:%Y-%m-%d}.jsonl"))

        def write() -> None:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")

        try:
            await self.hass.async_add_executor_job(write)
        except OSError as err:
            _LOGGER.warning("recording failed: %s", err)
