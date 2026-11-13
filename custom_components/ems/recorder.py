"""Record measurements continuously per quarter hour: /config/ems/messung/YYYY-MM-DD.jsonl.

Same fields as backtest/data/*.json (same computation, measurement.MeasurementWriter). This keeps the
data for calibration and backtests permanently, not just the ~10 days of HA history.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_POWER,
    CONF_GRID_POWER,
    CONF_LOAD_POWER,
    CONF_PV_POWER,
)
from .inputs import Inputs
from .measurement import MeasurementWriter

_LOGGER = logging.getLogger(__name__)


class MeasurementRecorder:
    def __init__(
        self,
        hass: HomeAssistant,
        conf: dict[str, Any],
        inputs: Inputs,
        price_now_ct: Callable[[], float | None],
    ) -> None:
        self.hass = hass
        self.conf = conf
        self.inputs = inputs
        self.price_now_ct = price_now_ct
        """Current price in ct/kWh (the coordinator: `preis_jetzt` entity or the price list)."""
        self.names = {
            entity_id: name
            for entity_id, name in (
                (conf.get(CONF_LOAD_POWER), "last"),
                (conf.get(CONF_PV_POWER), "pv"),
                (conf.get(CONF_BATTERY_POWER), "akku"),
                (conf.get(CONF_GRID_POWER), "netz"),
            )
            if entity_id
        }
        self.folder = Path(hass.config.path("ems", "messung"))
        self.writer = MeasurementWriter(dt_util.now())
        self._unsubscribe: list[Callable[[], None]] = []

    @callback
    def begin(self) -> None:
        now = dt_util.now()
        for eid, name in self.names.items():
            z = self.hass.states.get(eid)
            if z is not None:
                self.writer.value(name, now, z.state)
        self._on_quarter_start()
        self._on_mid()
        self._unsubscribe = [
            async_track_state_change_event(self.hass, list(self.names), self._changed),
            async_track_time_change(self.hass, self._on_boundary, minute=[0, 15, 30, 45], second=0),
            async_track_time_change(self.hass, self._on_mid_time, minute=[7, 22, 37, 52], second=30),
        ]

    @callback
    def stop(self) -> None:
        for f in self._unsubscribe:
            f()
        self._unsubscribe = []

    @callback
    def _on_quarter_start(self) -> None:
        """At the start of a quarter hour: remember SoC and price."""
        self.writer.extra_info(soc_percent=self.inputs.soc(), price_ct=self.price_now_ct())

    @callback
    def _on_mid(self) -> None:
        """In the middle of the quarter hour: what is set at the inverter."""
        self.writer.extra_info(action=self.inputs.inverter().action_code)

    @callback
    def _on_mid_time(self, _: Any) -> None:
        self._on_mid()

    @callback
    def _changed(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None:
            return
        self._finished(self.writer.value(self.names[event.data["entity_id"]], new.last_updated, new.state))

    @callback
    def _on_boundary(self, now: Any) -> None:
        self._finished(self.writer.boundary(dt_util.now()))

    @callback
    def _finished(self, finished: list[dict[str, Any]]) -> None:
        if not finished:
            return
        self._on_quarter_start()
        self.hass.async_create_task(self._write(finished))

    async def _write(self, lines: list[dict[str, Any]]) -> None:
        def write() -> None:
            self.folder.mkdir(parents=True, exist_ok=True)
            for z in lines:
                with (self.folder / f"{z['start'][:10]}.jsonl").open("a", encoding="utf-8") as f:
                    f.write(json.dumps(z, ensure_ascii=False) + "\n")

        try:
            await self.hass.async_add_executor_job(write)
        except OSError as err:
            _LOGGER.warning("could not write measurements: %s", err)
