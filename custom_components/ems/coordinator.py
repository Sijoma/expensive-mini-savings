"""Reads the inputs, plans in a worker thread and holds the result for the sensors."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .adapter import (
    PlanBlock,
    build_slots,
    plan_attributes,
    plan_blocks,
    running_action,
)
from .const import (
    CONF_FEED_IN_CT,
    CONF_PV,
    CONF_RECORDING,
)
from .control import PLAN_MAX_AGE_S
from .core.explanation import Plan, create_plan
from .core.model import Tariff
from .inputs import Inputs
from .prices import PriceSource
from .settings import PlannerSettings

_LOGGER = logging.getLogger(__name__)


RunningSource = Callable[[], tuple[str | None, datetime | None]]
"""What is applied at the battery right now and since when (the executor's target, or an external
`soll` entity): a running hold is not toggled by the plan."""


@dataclass(frozen=True)
class Result:
    plan: Plan
    attribute: dict[str, Any]
    blocks: list[PlanBlock]
    computed: datetime


class EmsCoordinator(DataUpdateCoordinator[Result]):
    def __init__(
        self,
        hass: HomeAssistant,
        conf: dict[str, Any],
        entry: ConfigEntry,
        inputs: Inputs,
        settings: PlannerSettings,
        prices: PriceSource,
        running: RunningSource,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name="EMS", update_interval=None)
        self.conf = conf
        self.inputs = inputs
        self.settings = settings
        self.price_source = prices
        self._running = running
        self._last_recording: str | None = None

    async def _async_update_data(self) -> Result:
        now = dt_util.now()
        profile = self.settings.load_profile()
        prices = await self.price_source.refresh(now)
        pv = self.inputs.attribute(CONF_PV, "forecast")
        slots = build_slots(prices, pv, profile, now)
        if not slots:
            raise UpdateFailed("no prices from now on")
        battery = self.settings.battery()
        soc = self.inputs.soc()
        if soc is None:
            raise UpdateFailed("battery state of charge unknown")
        e0 = soc / 100 * battery.capacity_kwh
        running, since = running_action(*self._running(), now)
        tariff = Tariff(feed_in_ct=float(self.conf.get(CONF_FEED_IN_CT, 7.0)))

        t0 = time.perf_counter()
        plan = await self.hass.async_add_executor_job(
            lambda: create_plan(slots, e0, battery, tariff, running=running, since=since)
        )
        runtime = (time.perf_counter() - t0) * 1000
        blocks = plan_blocks(plan, battery)
        attribute = plan_attributes(plan, battery, now, runtime, blocks)

        if self.conf.get(CONF_RECORDING):
            await self._record(now, soc, running, since, plan, attribute)
        return Result(plan, attribute, blocks, now)

    def latest_blocks(self) -> list[PlanBlock]:
        """Blocks of the last plan, also when it is no longer fresh (offers, approvals, booking)."""
        return list(self.data.blocks) if self.data else []

    def plan_fresh(self, now: datetime) -> bool:
        """The last calculation succeeded and is younger than PLAN_MAX_AGE_S (else: normal operation)."""
        if not self.data or not self.last_update_success:
            return False
        return (now - self.data.computed).total_seconds() < PLAN_MAX_AGE_S

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
