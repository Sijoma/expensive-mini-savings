"""Heat pump PV boost over SG Ready: what the rules want, and whether to switch, report or do nothing.

The rules of the author's former automation "Wärmepumpe: SG Ready bei PV-Überschuss", moved into the EMS
unchanged. SG Ready input "verstärkt" (the Stiebel's input 1 = state 3) raises room and hot water setpoints;
input "Sperre" (input 2 = state 1) blocks the heat pump and is never switched on.

No HA import, so every rule is a table or property test. The heat pump executor gathers the Readings, keeps
the Timers (how long a power has been above its threshold) and runs the Decision.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum

from .const import CONF_WP_BLOCK, CONF_WP_BOOST, CONF_WP_SG_READY
from .switching import FOREIGN_REPEAT_S, ForeignReport, Step

PLAN_ONLY = "Nur planen"
AUTOMATIC = "Automatisch"
MODES = (PLAN_ONLY, AUTOMATIC)
"""No approval mode: a boost only uses PV surplus that would otherwise be exported."""

BOOST = "PV-Boost"
NORMAL = "Normal"
BLOCKED = "Gesperrt"
UNREACHABLE = "Nicht erreichbar"

EXPORT_ON_W = 1200
"""On: export above this ..."""
CHARGE_ON_W = 2500
"""... or the battery charging with more than this ..."""
ON_AFTER_S = 600
"""... for 10 min."""
IMPORT_OFF_W = 300
"""Off: import above this, or the battery discharging with more than DISCHARGE_OFF_W, ..."""
DISCHARGE_OFF_W = 300
OFF_AFTER_S = 300
"""... for 5 min."""
PAUSE_S = 1800
"""After a boost ended, the next one waits this long."""
SOC_ABOVE = 80
"""Only with the battery above this (%): the surplus would otherwise still go into the battery."""
ON_FROM = time(9, 0)
ON_UNTIL = time(16, 30)
"""A boost starts only between these times ..."""
OFF_AT = time(17, 0)
"""... and ends at the latest at 17:00, before the expensive evening (Modul 3: 15–20 h)."""


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


@dataclass(frozen=True)
class Readings:
    """What the rules depend on, read now."""

    now: datetime
    grid_w: float | None
    """Grid power, + import, − export."""
    battery_w: float | None
    """Battery power, + discharging, − charging."""
    soc: float | None
    boosted: bool | None
    """Input "verstärkt" is on; None: no value."""
    blocked: bool | None
    """Input "Sperre" is on; None: no value. Not configured: False."""
    boosted_changed_at: datetime | None = None
    """Last change of input "verstärkt" (for the pause after a boost)."""
    operating_mode: str | None = None
    operating_mode_required: str | None = None
    """The heat pump's operating mode must be this (e.g. "program"); None: no condition."""


@dataclass(frozen=True)
class Timers:
    """Since when a power has been above its threshold without interruption (None: it is not)."""

    export_high: datetime | None = None
    charge_high: datetime | None = None
    import_high: datetime | None = None
    discharge_high: datetime | None = None


def _since(since: datetime | None, high: bool, now: datetime) -> datetime | None:
    if not high:
        return None
    return since if since is not None and since <= now else now


def advance(t: Timers, r: Readings) -> Timers:
    """Update the timers with a new reading. A missing or invalid value interrupts (never counts as high)."""
    grid, battery = _finite(r.grid_w), _finite(r.battery_w)
    return Timers(
        export_high=_since(t.export_high, grid is not None and -grid > EXPORT_ON_W, r.now),
        charge_high=_since(t.charge_high, battery is not None and -battery > CHARGE_ON_W, r.now),
        import_high=_since(t.import_high, grid is not None and grid > IMPORT_OFF_W, r.now),
        discharge_high=_since(t.discharge_high, battery is not None and battery > DISCHARGE_OFF_W, r.now),
    )


def _held(since: datetime | None, now: datetime, seconds: float) -> bool:
    return since is not None and (now - since).total_seconds() >= seconds


def rules(r: Readings, t: Timers) -> tuple[bool, str]:
    """Whether the rules want the boost on now, and why. Depends on whether it is on (on and off rules
    differ, so a boost does not flicker)."""
    now = r.now.time()
    if r.boosted:
        if now >= OFF_AT or now < ON_FROM:
            return False, "Spätestens um 17 Uhr zurück auf normal"
        if _held(t.import_high, r.now, OFF_AFTER_S):
            return False, f"Seit 5 min mehr als {IMPORT_OFF_W} W aus dem Netz"
        if _held(t.discharge_high, r.now, OFF_AFTER_S):
            return False, f"Akku entlädt seit 5 min mit mehr als {DISCHARGE_OFF_W} W"
        return True, "PV-Überschuss reicht weiter"
    if _held(t.export_high, r.now, ON_AFTER_S):
        why = f"Seit 10 min mehr als {EXPORT_ON_W} W Einspeisung"
    elif _held(t.charge_high, r.now, ON_AFTER_S):
        why = f"Akku lädt seit 10 min mit mehr als {CHARGE_ON_W} W"
    else:
        return False, "Kein dauerhafter PV-Überschuss"
    if not ON_FROM <= now < ON_UNTIL:
        return False, "PV-Boost beginnt nur zwischen 9:00 und 16:30"
    soc = _finite(r.soc)
    if soc is None or soc <= SOC_ABOVE:
        return False, f"Akku erst über {SOC_ABOVE} %, der Überschuss lädt ihn noch"
    if r.operating_mode_required is not None and r.operating_mode != r.operating_mode_required:
        return False, f"Betriebsart der Wärmepumpe ist {r.operating_mode}, nicht {r.operating_mode_required}"
    if r.boosted_changed_at is not None and (r.now - r.boosted_changed_at).total_seconds() < PAUSE_S:
        return False, "30 min Pause nach dem letzten PV-Boost"
    return True, why


def state_of(r: Readings) -> str:
    """What is set at the heat pump, in the app's terms."""
    if r.boosted is None or r.blocked is None:
        return UNREACHABLE
    if r.blocked:
        return BLOCKED
    return BOOST if r.boosted else NORMAL


class Kind(Enum):
    NOTHING = "nothing"
    SWITCH = "switch"
    REPORT_FOREIGN = "report_foreign"
    OWN_ENDED = "own_ended"
    """The EMS's boost was ended by someone else: forget it, do not switch."""


@dataclass(frozen=True)
class Decision:
    kind: Kind
    on: bool = False
    reason: str = ""


NOTHING = Decision(Kind.NOTHING)


@dataclass(frozen=True)
class Situation:
    mode: str
    readings: Readings
    timers: Timers
    set_by_ems: bool
    """The EMS switched the boost on (and has not switched it off since)."""
    trigger: str
    """"pruefen" (periodic) or "modus" (mode changed)."""
    switch_failed: bool = False
    """The last own switch was not confirmed: a boost that is on may be the EMS's half-done command."""
    foreign_reported: ForeignReport | None = None


def decide(s: Situation) -> Decision:
    """Switch, report a setting that did not come from the EMS (never override it), or do nothing.
    In "Nur planen" nothing is switched; only an own boost is taken back when the mode changes."""
    r = s.readings
    if r.boosted is None or r.blocked is None:
        return NOTHING
    if s.set_by_ems and not r.boosted:
        return Decision(Kind.OWN_ENDED, reason="Boost von außen beendet")
    if s.mode != AUTOMATIC:
        if s.trigger == "modus" and s.set_by_ems and r.boosted:
            return Decision(Kind.SWITCH, False, "Modus „Nur planen“: eigener PV-Boost zurück")
        return NOTHING
    # The EMS never switches the block on, so a block is always foreign. A boost the EMS did not confirm
    # may be its own half-done command (switch_failed): then it may correct it.
    if r.blocked or (r.boosted and not s.set_by_ems and not s.switch_failed):
        setting = state_of(r)
        last = s.foreign_reported
        if last and last.setting == setting and (r.now - last.at).total_seconds() <= FOREIGN_REPEAT_S:
            return NOTHING
        return Decision(Kind.REPORT_FOREIGN, reason=setting)
    want, why = rules(r, s.timers)
    if want == r.boosted:
        return NOTHING
    return Decision(Kind.SWITCH, want, why)


def commands(on: bool, *, sg_ready: bool, block: bool) -> list[Step]:
    """The switch commands, in order. `sg_ready` / `block`: those entities are configured. The block input is
    only ever switched off."""
    if on:
        return [
            *([Step("switch", CONF_WP_BLOCK, False)] if block else []),
            *([Step("switch", CONF_WP_SG_READY, True)] if sg_ready else []),
            Step("switch", CONF_WP_BOOST, True),
        ]
    return [
        Step("switch", CONF_WP_BOOST, False),
        *([Step("switch", CONF_WP_BLOCK, False)] if block else []),
    ]
