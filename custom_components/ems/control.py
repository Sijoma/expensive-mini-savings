"""Executor rules: what should run at the battery now, approvals, booking, load profile.

No HA import, so every rule is testable. This is the logic of the author's former YAML package
(sensor.ems_akku_soll, binary_sensor.ems_akku_freigabe_offen, the approval scripts, the booking in
automation.ems_akku_steuern, the load profile template), moved into the integration unchanged.

Blocks are the plan's PlanBlocks (adapter.py): start/end in ms, code h/l, kwh, ct, saves_eur, grid_kwh.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from .adapter import PlanBlock
from .inverter import (  # noqa: F401  (option names are part of control's interface)
    FORCED_CHARGE,
    FORCED_MODE,
    FORCED_STOP,
    HOLD_MAX_W,
    SELF_CONSUMPTION,
    InverterSetting,
    classify,
)

NORMAL = "Eigenverbrauch"
HOLD = "Entladung gesperrt"
CHARGE = "Laden aus dem Netz"
NAME = {"h": HOLD, "l": CHARGE}
CODE = {HOLD: "h", CHARGE: "l"}

PLAN_ONLY = "Nur planen"
WITH_APPROVAL = "Mit Freigabe"
AUTOMATIC = "Automatisch"
MODES = (PLAN_ONLY, WITH_APPROVAL, AUTOMATIC)

MIN_MS = {"h": 30 * 60_000, "l": 15 * 60_000}
"""Shortest block that is run (or offered): hold 30 min, grid charge 15 min. A running block is
finished even if the plan shortens it."""
MIN_SAVING_EUR = 0.05
"""Blocks saving less are neither run nor offered (unless already running)."""
PLAN_MAX_AGE_S = 1800
"""Older plan → normal operation."""
CHARGE_STOP_SOC = 98
"""Grid charging stops at this state of charge."""
APPROVED_KWH_FACTOR = 1.25
APPROVED_KWH_MARGIN = 0.5
"""Grid charging only up to the approved amount + 25 % + 0.5 kWh (the plan may move a little)."""
APPROVED_KWH_MAX = 20.0
DAY_MS = 86_400_000
APPROVAL_UNTIL_HOUR = 14
"""Approvals last until the next 14:00 (new prices, new plan), at least to the end of the last block."""


@dataclass(frozen=True)
class Approval:
    until_ms: int = 0
    codes: frozenset[str] = field(default_factory=frozenset)
    kwh: float = 0.0
    rejected_until_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "until_ms": self.until_ms,
            "codes": sorted(self.codes),
            "kwh": self.kwh,
            "rejected_until_ms": self.rejected_until_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Approval:
        d = d or {}
        return cls(
            until_ms=int(d.get("until_ms") or 0),
            codes=frozenset(d.get("codes") or ()),
            kwh=float(d.get("kwh") or 0.0),
            rejected_until_ms=int(d.get("rejected_until_ms") or 0),
        )


def _long_enough(b: PlanBlock) -> bool:
    return b.end_ms - b.start_ms >= MIN_MS.get(b.code, 0)


def _worth_it(b: PlanBlock) -> bool:
    return b.saves_eur is None or b.saves_eur >= MIN_SAVING_EUR


def charge_kwh_until(blocks: Iterable[PlanBlock], now_ms: int, until_ms: int) -> float:
    """Grid charge planned from now until `until_ms`, counting only offered blocks (next 24 h, long enough,
    worth it): a block that cannot be approved yet must not make an approval look exceeded."""
    return sum(b.kwh or 0.0 for b in offered(blocks, now_ms) if b.code == "l" and b.start_ms < until_ms)


def _over_approved_kwh(blocks: Sequence[PlanBlock], now_ms: int, approval: Approval) -> bool:
    limit = approval.kwh * APPROVED_KWH_FACTOR + APPROVED_KWH_MARGIN
    return charge_kwh_until(blocks, now_ms, approval.until_ms) > limit


def _approved(result: str, blocks: Sequence[PlanBlock], now_ms: int, approval: Approval) -> bool:
    if now_ms > approval.until_ms or CODE[result] not in approval.codes:
        return False
    return not (result == CHARGE and _over_approved_kwh(blocks, now_ms, approval))


def target(
    mode: str,
    blocks: Sequence[PlanBlock],
    now_ms: int,
    plan_fresh: bool,
    approval: Approval,
    active: str,
    soc: float | None,
) -> str:
    """What should run at the battery now. `active` is the current target (a running block is
    finished even when it became too short or too small)."""
    result = NORMAL
    if mode not in (WITH_APPROVAL, AUTOMATIC) or not plan_fresh:
        return result
    for b in blocks:
        if b.code not in NAME or not b.start_ms <= now_ms < b.end_ms:
            continue
        running = NAME[b.code] == active
        if (_long_enough(b) or running) and (_worth_it(b) or running):
            result = NAME[b.code]
    if mode == WITH_APPROVAL and result != NORMAL and not _approved(result, blocks, now_ms, approval):
        result = NORMAL
    if result == CHARGE and soc is None:
        result = NORMAL
    elif result == CHARGE and soc >= CHARGE_STOP_SOC:
        # full during an approved grid charge: keep the charge instead of discharging it into the cheap
        # hours that are left (holding does less than charging, so it needs no extra approval)
        result = HOLD
    return result


def offered(blocks: Sequence[PlanBlock], now_ms: int) -> list[PlanBlock]:
    """Blocks of the next 24 h that would be run: these are approved or rejected together."""
    return [
        b
        for b in blocks
        if b.code in NAME
        and b.end_ms > now_ms
        and b.start_ms < now_ms + DAY_MS
        and _long_enough(b)
        and _worth_it(b)
    ]


def pending(mode: str, blocks: Sequence[PlanBlock], now_ms: int, approval: Approval) -> bool:
    """In mode 'Mit Freigabe': is something waiting for approval?"""
    if mode != WITH_APPROVAL:
        return False
    waiting = [
        b
        for b in offered(blocks, now_ms)
        if not (b.end_ms <= approval.until_ms and b.code in approval.codes)
        and not b.start_ms < approval.rejected_until_ms
    ]
    # more grid charge than approved only matters while an approval holds (not after it expired or a
    # rejection ended it: a running block the user rejected must not stay pending)
    over = now_ms <= approval.until_ms and _over_approved_kwh(blocks, now_ms, approval)
    return bool(waiting) or over


REQUEST_AFTER_S = 300
"""Approval requests are sent when something has been pending this long ..."""
REQUEST_HOURS = (7, 22.5)
"""... and only between 07:00 and 22:30 (at 07:00 open requests come again)."""


def should_request(now: datetime, pending_since: datetime | None, requested_at: datetime | None) -> bool:
    """Send an approval request now? Once something has been pending for 5 min (if not already asked
    since it became pending), and again at 07:00; never at night."""
    if pending_since is None:
        return False
    hour = now.hour + now.minute / 60
    if not REQUEST_HOURS[0] <= hour < REQUEST_HOURS[1]:
        return False
    morning = now.hour == REQUEST_HOURS[0] and now.minute == 0
    waited = (now - pending_since).total_seconds() >= REQUEST_AFTER_S
    return ((requested_at is None or requested_at < pending_since) and waited) or morning


def next_approval_end(now: datetime) -> datetime:
    """The next 14:00 (local time of `now`)."""
    at = now.replace(hour=APPROVAL_UNTIL_HOUR, minute=0, second=0, microsecond=0)
    return at if now < at else at + timedelta(days=1)


def approval_end(items: Sequence[PlanBlock], now: datetime) -> int:
    """How long an approval of these blocks holds (ms): the next 14:00, at least to the end of the last."""
    return max(max(b.end_ms for b in items), int(next_approval_end(now).timestamp() * 1000))


def approve(blocks: Sequence[PlanBlock], now: datetime, approval: Approval) -> Approval:
    """Approve everything offered now: until the next 14:00, at least to the end of the last block."""
    now_ms = int(now.timestamp() * 1000)
    items = offered(blocks, now_ms)
    if not items:
        return approval
    until = approval_end(items, now)
    kwh = round(min(sum(b.kwh or 0.0 for b in items if b.code == "l"), APPROVED_KWH_MAX), 1)
    return Approval(until_ms=until, codes=frozenset(b.code for b in items), kwh=kwh, rejected_until_ms=now_ms)


def reject(blocks: Sequence[PlanBlock], now: datetime, approval: Approval) -> Approval:
    """Reject everything offered now (until its end); an earlier approval ends now."""
    now_ms = int(now.timestamp() * 1000)
    items = offered(blocks, now_ms)
    if not items:
        return approval
    # ends just before now: "after the approval time" must already hold in this millisecond
    return replace(approval, until_ms=now_ms - 1, rejected_until_ms=max(b.end_ms for b in items))


def withdraw(now: datetime, approval: Approval) -> Approval:
    """Take back an approval: from now on nothing approved runs."""
    return replace(approval, until_ms=int(now.timestamp() * 1000) - 1)


# ---- inverter ---------------------------------------------------------------------------------

HOLD_DISCHARGE_W = 10
"""Discharge limit for a hold: the minimum of the SBH battery; PV surplus keeps charging."""
UNREACHABLE = "Nicht erreichbar"
"""The inverter's mode entities report no value (integration down, Modbus timeout)."""
STATE = {
    InverterSetting.NORMAL: NORMAL,
    InverterSetting.HOLD: HOLD,
    InverterSetting.CHARGE: CHARGE,
    InverterSetting.UNREACHABLE: UNREACHABLE,
}


def inverter_state(mode: str | None, forced: str | None, discharge_w: float | None) -> str:
    """What is set at the inverter, in the executor's terms (or 'Anders (…)' for anything else)."""
    setting = classify(mode, forced, discharge_w)
    if setting in STATE:
        return STATE[setting]
    g = discharge_w if discharge_w is not None else -1
    shown = int(g) if math.isfinite(g) else g  # an entity may report inf/nan; never fail on the text
    return f"Anders ({mode} / {forced} / {shown} W)"


# ---- booking ----------------------------------------------------------------------------------

GRID_TO_BATTERY = 0.961
"""Measured efficiency grid → battery (charged amount is DC in the battery)."""


@dataclass(frozen=True)
class Counters:
    """Meter readings (kWh) at a point in time."""

    charged: float
    charged_pv: float
    imported: float

    @property
    def charged_from_grid(self) -> float:
        return self.charged - self.charged_pv


def start_record(code: str, at_iso: str, counters: Counters, block: PlanBlock | None) -> dict[str, Any]:
    """What is remembered when a block starts: meters and the plan's savings and quantity."""
    return {
        "action": code,
        "start": at_iso,
        "grid_charged_kwh": round(counters.charged_from_grid, 3),
        "imported_kwh": round(counters.imported, 3),
        "saves_eur": block.saves_eur if block else None,
        "planned_kwh": (block.kwh if code == "l" else block.grid_kwh) if block else None,
    }


def book(record: dict[str, Any], counters: Counters) -> tuple[float, float]:
    """(measured kWh, EUR) of a finished block: the plan's savings for the block, scaled by the measured
    quantity (grid charge: kWh from the grid, AC; hold: grid import), at most the planned value."""
    charge = record["action"] == "l"
    charged = round(counters.charged_from_grid - record["grid_charged_kwh"], 3)
    imported = round(counters.imported - record["imported_kwh"], 3)
    measured = max(charged, 0.0) / GRID_TO_BATTERY if charge else max(imported, 0.0)
    shown = max(charged if charge else imported, 0.0)
    saves, planned = record.get("saves_eur"), record.get("planned_kwh")
    if not isinstance(saves, int | float):
        return shown, 0.0
    if isinstance(planned, int | float) and planned > 0:
        return shown, round(saves * min(measured / planned, 1.0), 3)
    return shown, round(saves, 3) if measured > 0 else 0.0


# ---- load profile -----------------------------------------------------------------------------

START_PROFILE = [300, 280, 300, 330, 330, 400, 650, 900, 800, 750, 800, 850,
                 1100, 1000, 900, 650, 600, 650, 800, 1000, 700, 600, 550, 400]  # fmt: skip
"""Start values (W per hour of day), roughly from 24.10.–07.11.2026 in the author's house."""
LEARN_SHARE = 0.15
MAX_W = 15000


def learn_profile(
    profile: Sequence[float],
    meter_before: float | None,
    at_before: datetime | None,
    meter_now: float | None,
    at_now: datetime,
) -> list[float]:
    """Blend the average power of the past hour into that hour's value (15 % new). Readings that are not
    about one hour apart, or implausible, change nothing."""
    profile = list(profile) if len(profile) == 24 else list(START_PROFILE)
    if meter_before is None or meter_now is None or at_before is None:
        return profile
    seconds = (at_now - at_before).total_seconds()
    if not 3000 < seconds < 4200:
        return profile
    w = (meter_now - meter_before) * 1000 * 3600 / seconds
    if not 0 <= w < MAX_W:
        return profile
    h = at_before.hour
    profile[h] = round(profile[h] * (1 - LEARN_SHARE) + w * LEARN_SHARE)
    return profile
