"""The heat pump part of the executor: switches the PV boost over SG Ready (rules in heat_pump_control.py).

Shares the battery executor's store (state["heat_pump"]), log and confirmation. Mode "Nur planen" (default)
switches nothing and only shows what the rules want; "Automatisch" switches. While the author's former
automation is still on, this part stays passive (never two controllers).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, callback
from homeassistant.util import dt as dt_util

from . import heat_pump_control as hp
from .const import CONF_WP_BLOCK, CONF_WP_BOOST, CONF_WP_SG_READY
from .switching import ForeignReport

if TYPE_CHECKING:
    from .executor import Executor

_LOGGER = logging.getLogger(__name__)

LEGACY_AUTOMATION = "automation.warmepumpe_sg_ready_bei_pv_uberschuss"
"""The author's former automation for the PV boost (up to 0.10)."""
PREFIX = "Wärmepumpe: "


def empty_state() -> dict[str, Any]:
    return {"mode": hp.PLAN_ONLY, "set_by_ems": False, "switch_failed": False, "foreign_reported": None}


class HeatPumpExecutor:
    def __init__(self, executor: Executor) -> None:
        self.ex = executor
        self.hass = executor.hass
        self.conf = executor.conf
        self.inputs = executor.inputs
        self.timers = hp.Timers()
        self._warned_legacy = False

    # ---- state ----

    @property
    def state(self) -> dict[str, Any]:
        if not isinstance(self.ex.state.get("heat_pump"), dict):
            self.ex.state["heat_pump"] = empty_state()
        return self.ex.state["heat_pump"]

    @property
    def mode(self) -> str:
        mode = self.state.get("mode")
        return mode if mode in hp.MODES else hp.PLAN_ONLY

    def readings(self) -> hp.Readings:
        return self.inputs.heat_pump(dt_util.now())

    def wanted(self) -> tuple[bool, str]:
        """What the rules want now (also in "Nur planen")."""
        r = self.readings()
        return hp.rules(r, hp.advance(self.timers, r))

    def current(self) -> str:
        return hp.state_of(self.readings())

    # ---- events ----

    def entities(self) -> list[str]:
        return [*self.inputs.power_entities(), *self.inputs.heat_pump_entities()]

    @callback
    def power_changed(self, _event: Event | None = None) -> None:
        """Every new power value advances the timers, like the automation's "for: 10 min"."""
        self.timers = hp.advance(self.timers, self.readings())

    @callback
    def every_minute(self, _now: datetime) -> None:
        self.power_changed()
        self.hass.async_create_task(self.apply("pruefen"))

    async def set_mode(self, mode: str) -> None:
        if mode not in hp.MODES or mode == self.mode:
            return
        old = self.mode
        self.state["mode"] = mode
        self.ex.log("Modus", f"{PREFIX}Modus {old} → {mode}")
        self.ex._changed()
        await self.apply("modus")

    # ---- switching ----

    def legacy_on(self) -> bool:
        """The former automation is loaded and on."""
        state = self.hass.states.get(LEGACY_AUTOMATION)
        return state is not None and state.state == "on" and not state.attributes.get("restored")

    def legacy_running(self) -> bool:
        """legacy_on, noted once in the log while the mode is "Automatisch"."""
        running = self.legacy_on()
        if running and not self._warned_legacy and self.mode == hp.AUTOMATIC:
            self._warned_legacy = True
            _LOGGER.warning("EMS: %s is on, the integration does not switch the heat pump", LEGACY_AUTOMATION)
            self.ex.log("Hinweis", f"{PREFIX}Alte PV-Boost-Automation ist noch an, das EMS schaltet nicht.")
            self.ex._changed()
        if not running:
            self._warned_legacy = False
        return running

    async def apply(self, trigger: str) -> None:
        async with self.ex._lock:
            await self._apply(trigger)

    async def _apply(self, trigger: str) -> None:
        if self.legacy_running():
            return
        self.power_changed()
        r = self.readings()
        situation = hp.Situation(
            mode=self.mode,
            readings=r,
            timers=self.timers,
            set_by_ems=bool(self.state.get("set_by_ems")),
            trigger=trigger,
            switch_failed=bool(self.state.get("switch_failed")),
            foreign_reported=ForeignReport.from_dict(self.state.get("foreign_reported")),
        )
        decision = hp.decide(situation)
        if decision.kind is hp.Kind.NOTHING:
            return
        if decision.kind is hp.Kind.OWN_ENDED:
            self.state["set_by_ems"] = False
            self.ex.log("Manuell", f"{PREFIX}PV-Boost wurde von außen beendet")
            self.ex._changed()
            return
        if decision.kind is hp.Kind.REPORT_FOREIGN:
            self.state["foreign_reported"] = ForeignReport(decision.reason, r.now).as_dict()
            self.ex.log(
                "Manuell", f"{PREFIX}steht auf „{decision.reason}“ (nicht vom EMS), EMS greift nicht ein"
            )
            self.ex._changed()
            return
        await self._switch(decision.on, decision.reason)

    async def _switch(self, on: bool, reason: str) -> None:
        steps = hp.commands(
            on, sg_ready=bool(self.conf.get(CONF_WP_SG_READY)), block=bool(self.conf.get(CONF_WP_BLOCK))
        )
        for step in steps:
            service = "turn_on" if step.value else "turn_off"
            await self.hass.services.async_call(
                "switch", service, {"entity_id": self.conf[step.key]}, blocking=True
            )
        goal = hp.BOOST if on else hp.NORMAL
        entities = [self.conf[k] for k in (CONF_WP_BOOST, CONF_WP_BLOCK) if self.conf.get(k)]
        if await self.ex.wait_for(entities, lambda: self.current() == goal):
            self.state["set_by_ems"] = on
            self.state["switch_failed"] = False
            self.ex.log("Geschaltet", f"{PREFIX}{hp.NORMAL if on else hp.BOOST} → {goal} ({reason})")
        else:
            self.state["switch_failed"] = True
            self.ex.log("Fehler", f"{PREFIX}Umschalten auf {goal} nicht bestätigt, meldet {self.current()}")
        self.ex._changed()
