"""Switching decisions at the inverter: whether to switch, report a foreign setting or do nothing, and which
values to send in which order.

No HA import, so every case is a table test. The executor gathers the Situation, runs the Decision
(commands, confirmation, log, push) and remembers `switch_failed` and `foreign_reported`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from . import control as c
from .const import CONF_DISCHARGE_MAX, CONF_EMS_MODE, CONF_FORCED, CONF_FORCED_POWER, GRID_KW_MAX

FOREIGN_QUIET_S = 300
"""A setting that did not come from the EMS is reported once it has lasted this long ..."""
FOREIGN_REPEAT_S = 12 * 3600
"""... and the same one at most every 12 h."""
NORMAL_DISCHARGE_W = 5900
"""Normal discharge limit when the number entity reports no upper bound (SBH100)."""


@dataclass(frozen=True)
class ForeignReport:
    """The last reported foreign setting: which one and when."""

    setting: str
    at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {"setting": self.setting, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> ForeignReport | None:
        if not d or not d.get("setting") or not d.get("at"):
            return None
        try:
            return cls(d["setting"], datetime.fromisoformat(d["at"]))
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Situation:
    """Everything the switching decision depends on."""

    mode: str
    target: str
    """What should run according to plan and approvals (control.target)."""
    current: str
    """What the inverter reports (control.inverter_state)."""
    ready: bool
    """All inverter entities report a value."""
    set_by_ems: str
    """What the EMS set last."""
    trigger: str
    """Why the decision is made: "soll" (target changed), "modus", "pruefen" (periodic check)."""
    now: datetime
    switch_failed: bool = False
    """The last own switch was not confirmed: the setting at the inverter may be a half-done command."""
    foreign_reported: ForeignReport | None = None
    inverter_changed_at: datetime | None = None
    """Last change of any inverter entity."""


class Kind(Enum):
    NOTHING = "nothing"
    SWITCH = "switch"
    REPORT_FOREIGN = "report_foreign"


@dataclass(frozen=True)
class Decision:
    kind: Kind
    goal: str = c.NORMAL
    current: str = c.NORMAL


NOTHING = Decision(Kind.NOTHING)


def decide(s: Situation) -> Decision:
    """Switch to the goal, report a setting that did not come from the EMS (never override it), or do
    nothing. In "Nur planen" the goal is normal operation, and only the EMS's own setting is taken back,
    and only when the mode changes."""
    active = s.mode in (c.WITH_APPROVAL, c.AUTOMATIC)
    goal = s.target if active else c.NORMAL
    if not s.ready or goal == s.current:
        return NOTHING
    if not active and not (s.trigger == "modus" and s.current == s.set_by_ems and s.current != c.NORMAL):
        return NOTHING
    if s.current in (s.set_by_ems, c.NORMAL) or s.switch_failed:
        return Decision(Kind.SWITCH, goal, s.current)
    if s.inverter_changed_at and (s.now - s.inverter_changed_at).total_seconds() <= FOREIGN_QUIET_S:
        return NOTHING
    last = s.foreign_reported
    if last and last.setting == s.current and (s.now - last.at).total_seconds() <= FOREIGN_REPEAT_S:
        return NOTHING
    return Decision(Kind.REPORT_FOREIGN, goal, s.current)


@dataclass(frozen=True)
class Step:
    """One command: "select" an option or set a "number", on the entity of a config key."""

    kind: str
    key: str
    value: Any


def commands_for(
    goal: str, *, grid_kw: float, power_configured: bool, normal_discharge_w: int = NORMAL_DISCHARGE_W
) -> list[Step]:
    """The commands for a goal, in the order they must be sent."""
    if goal == c.CHARGE:
        steps = (
            [Step("number", CONF_FORCED_POWER, int(min(grid_kw, GRID_KW_MAX) * 1000))]
            if power_configured
            else []
        )
        return [
            *steps,
            Step("select", CONF_FORCED, c.FORCED_CHARGE),
            Step("select", CONF_EMS_MODE, c.FORCED_MODE),
        ]
    if goal == c.HOLD:
        # first the discharge limit (the battery's minimum), then self-consumption: the battery does not
        # discharge, PV surplus keeps charging it
        return [
            Step("number", CONF_DISCHARGE_MAX, c.HOLD_DISCHARGE_W),
            Step("select", CONF_FORCED, c.FORCED_STOP),
            Step("select", CONF_EMS_MODE, c.SELF_CONSUMPTION),
        ]
    return [
        Step("select", CONF_FORCED, c.FORCED_STOP),
        Step("select", CONF_EMS_MODE, c.SELF_CONSUMPTION),
        Step("number", CONF_DISCHARGE_MAX, normal_discharge_w),
    ]
