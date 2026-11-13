"""Translation between Home Assistant states and the core. No HA import, so it is testable.

Inputs:
- prices: attribute `preise` = [[ms, ct], …], or the response of the action tibber.get_prices
- PV: attribute `forecast` = [{datetime, watts, p10}, …] per 15 min (Helios, DC)
- consumption: attribute `profil` = 24 values in W × factor

The output attributes (German keys such as `aktionen`, `grund`) are the interface to the author's
YAML package and dashboard.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .core.explanation import NAMES, Plan
from .core.forecast import SPIKE_SHARE, prepare
from .core.model import DT_H, MIN_HOLD_SLOTS, Action, Battery, Slot, Tariff
from .core.optimizer import SWITCH_CT
from .core.simulation import flows, residual_per_kwh
from .measurement import as_number

FLOWS = ("pv_house", "battery_house", "grid_house", "pv_battery", "pv_grid", "grid_battery")
"""Columns 10–15 of slots (kW): where the house gets its power from and where PV goes."""

QUARTER_MS = 900_000
P10_SHARE = 0.15
"""PV forecast: 85 % mean + 15 % P10."""
LOAD_FACTOR = 1.1
"""Factor on the load profile. Did better than 1.0 in the backtest."""


def ct_from_eur(eur_per_kwh: float) -> float:
    """EUR/kWh → ct/kWh, 0.01 ct (the one conversion for prices)."""
    return round(eur_per_kwh * 100, 2)


def prices_from_tibber(response: Mapping[str, Any] | None) -> list[list[float]]:
    """Response of the action tibber.get_prices → [[ms, ct], …] of the first home.

    The response is {"prices": {home name: [{"start_time": ISO time or datetime, "price": EUR/kWh}, …]}}.
    """
    homes = response.get("prices") if isinstance(response, Mapping) else None
    entries = next(iter(homes.values()), None) if isinstance(homes, Mapping) else None
    prices = []
    for e in entries if isinstance(entries, list) else []:
        try:
            start = e["start_time"]
            start = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
            price = float(e["price"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if math.isfinite(price):  # a format change must not reach the plan
            prices.append([int(start.timestamp() * 1000), ct_from_eur(price)])
    return sorted(prices)


def price_at(prices: list[list[float]] | None, now: datetime) -> float | None:
    """Price in ct/kWh of the quarter hour that contains `now`, from [[ms, ct], …]."""
    now_ms = int(now.timestamp() * 1000)
    slot = now_ms - now_ms % QUARTER_MS
    return next((float(p[1]) for p in prices or [] if len(p) >= 2 and p[0] == slot), None)


def build_slots(
    prices: list[list[float]] | None,
    pv: list[Mapping[str, Any]] | None,
    profile: list[float] | None,
    now: datetime,
    *,
    p10_share: float = P10_SHARE,
    load_factor: float = LOAD_FACTOR,
    spike_share: float = SPIKE_SHARE,
) -> list[Slot]:
    """Quarter hours from the current one to the end of the known prices."""
    now_ms = int(now.timestamp() * 1000)
    slot0 = now_ms - now_ms % QUARTER_MS
    pv_by_ms: dict[int, float] = {}
    for f in pv or []:
        try:
            t = int(datetime.fromisoformat(f["datetime"]).timestamp() * 1000)
        except (KeyError, TypeError, ValueError):
            continue
        w50 = as_number(f.get("watts"), 0.0)
        w10 = as_number(f.get("p10"), w50)
        pv_by_ms[t] = ((1 - p10_share) * w50 + p10_share * w10) / 1000
    profile = profile if profile and len(profile) == 24 else [500.0] * 24
    slots = []
    for p in prices or []:
        if len(p) < 2 or p[0] < slot0:
            continue
        start = datetime.fromtimestamp(p[0] / 1000, tz=now.tzinfo)
        load = as_number(profile[start.hour], 500.0) / 1000 * load_factor
        slots.append(Slot(start, float(p[1]), pv_by_ms.get(int(p[0]), 0.0), load))
    slots.sort(key=lambda s: s.start)
    return prepare(slots, spike_share)


def running_action(target: str | None, since: datetime | None, now: datetime) -> tuple[Action | None, int]:
    """What the EMS currently wants at the battery (target entity) and for how many quarter hours."""
    names = {v: k for k, v in NAMES.items()}
    action = names.get(target or "")
    if action in (None, Action.NORMAL):
        return None, 0
    quarter = int((now - since) / timedelta(minutes=15)) + 1 if since else 2
    return action, max(quarter, 1)


def savings_24h(plan: Plan, battery: Battery, quarters: int = 96) -> float:
    """Savings (ct) of the next 24 h: costs without minus with plan, plus the value of the extra
    content the plan leaves in the battery after 24 h (valued like the optimizer values the end of the
    horizon). Without that term a night charge counts as cost, but its use the next day does not."""
    n = min(quarters, len(plan.slots))
    if n == 0:
        return 0.0
    cost = sum(x.cost_ct for x in plan.without.steps[:n]) - sum(x.cost_ct for x in plan.with_plan.steps[:n])
    extra_kwh = plan.with_plan.steps[n - 1].e_after - plan.without.steps[n - 1].e_after
    return cost + extra_kwh * residual_per_kwh(plan.slots, battery, Tariff())


@dataclass(frozen=True)
class PlanBlock:
    """A block of the plan in time and money: what the executor runs, offers and books.

    The core Block counts in slot indices and ct; this is the same block with times (ms since epoch),
    the quantities the executor needs and the values already rounded as shown in the app. Built once by
    plan_blocks(); as_attribute() is the external form (sensor and app, German keys)."""

    start_ms: int
    end_ms: int
    code: str
    """Action code: "h" hold (discharge blocked), "l" grid charge."""
    kwh: float = 0.0
    """Grid charge in kWh (0 for a hold)."""
    ct: float = 0.0
    """Average price of the block, ct/kWh."""
    value_ct: float = 0.0
    """Value of one kWh in the battery during the block, ct."""
    for_ct: float = 0.0
    """Price the kept kWh replaces later (value + wear, after discharge losses), ct."""
    gain_ct: float | None = None
    """Grid charge only: gain per kWh charged from the grid, ct."""
    saves_eur: float | None = None
    """Planned savings of the block in EUR (None: unknown, counts as worth it)."""
    grid_kwh: float | None = None
    """Hold only: grid import the hold causes, kWh (the measure for booking)."""
    short: bool = False
    """Shorter than 30 min."""
    reason: str = ""
    calculation: str = ""

    def as_attribute(self) -> dict[str, Any]:
        return {
            "von": self.start_ms,
            "bis": self.end_ms,
            "was": self.code,
            "kwh": self.kwh,
            "ct": self.ct,
            "wert_ct": self.value_ct,
            "fuer_ct": self.for_ct,
            "vorteil": self.gain_ct,
            "spart": self.saves_eur,
            "netz_kwh": self.grid_kwh,
            "kurz": self.short,
            "grund": self.reason,
            "rechnung": self.calculation,
        }


def plan_blocks(plan: Plan, battery: Battery) -> list[PlanBlock]:
    """The plan's blocks with times, quantities and rounded values."""
    ms = [int(s.start.timestamp() * 1000) for s in plan.slots]
    return [
        PlanBlock(
            start_ms=ms[b.first],
            end_ms=ms[b.until - 1] + QUARTER_MS,
            code=b.action.value,
            kwh=round(b.grid_kwh, 1) if b.action is Action.GRID_CHARGE else 0,
            ct=round(b.price_ct, 1),
            value_ct=round(b.value_ct, 1),
            for_ct=round((b.value_ct + battery.wear_ct) / battery.efficiency_discharge, 1),
            gain_ct=round(battery.efficiency_grid_charge * b.value_ct - b.price_ct, 1)
            if b.action is Action.GRID_CHARGE
            else None,
            saves_eur=round(b.saves_ct / 100, 2),
            grid_kwh=round(b.grid_kwh, 2) if b.action is Action.HOLD else None,
            short=(b.until - b.first) * DT_H < 0.5,
            reason=b.reason,
            calculation=b.calculation,
        )
        for b in plan.blocks
    ]


def plan_attributes(
    plan: Plan,
    battery: Battery,
    computed: datetime,
    runtime_ms: float,
    blocks: list[PlanBlock] | None = None,
) -> dict[str, Any]:
    """Attributes of the plan sensor (sensor.ems_plan_akku).

    `slots`: [0 ms, 1 ct, 2 PV kW, 3 load kW, 4 SoC %, 5 battery code, 6 heat pump code ("n"/"pv"),
    7 grid charge kWh, 8 value of one kWh in the battery (ct), 9 SoC without control %, 10–15 flows
    in kW: PV→house, battery→house, grid→house, PV→battery, PV→grid, grid→battery].
    `wp_aktionen`: periods with heat pump PV boost. `aktionen`: the plan blocks, PlanBlock.as_attribute()."""
    ms = [int(s.start.timestamp() * 1000) for s in plan.slots]
    rows = [
        [
            ms[i],
            round(s.price_ct, 2),
            round(s.pv_kw, 2),
            round(s.load_kw, 2),
            round(plan.with_plan.steps[i].e_after / battery.capacity_kwh * 100),
            plan.actions[i].value,
            "pv" if plan.boost[i] else "n",
            round(plan.with_plan.steps[i].grid_charged_kwh, 2),
            round(plan.value_ct[i]),
            round(plan.without.steps[i].e_after / battery.capacity_kwh * 100),
            *(f[k] for k in FLOWS),
        ]
        for i, s in enumerate(plan.slots)
        for f in [flows(plan.with_plan.steps[i], s, battery)]
    ]
    actions = [b.as_attribute() for b in (plan_blocks(plan, battery) if blocks is None else blocks)]
    heat_pump_actions, start = [], None
    for i, b in enumerate([*plan.boost, False]):
        if b and start is None:
            start = i
        elif not b and start is not None:
            heat_pump_actions.append({"von": ms[start], "bis": ms[i - 1] + QUARTER_MS, "was": "pv"})
            start = None
    return {
        "slots": rows,
        "aktionen": actions,
        "wp_aktionen": heat_pump_actions,
        "grund": plan.reason_now,
        "wert_jetzt_ct": round(plan.value_ct[0], 1) if plan.value_ct else None,
        "ersparnis_eur": round(plan.saves_ct / 100, 2),
        # next 24 h, for sensor.ems_prognose_ersparnis (trial run)
        "ersparnis_24h": round(savings_24h(plan, battery) / 100, 3),
        "netzladen_kwh": round(sum(x.grid_charged_kwh for x in plan.with_plan.steps), 1),
        "kosten_ohne_eur": round(plan.without.valuation_ct / 100, 2),
        "kosten_plan_eur": round(plan.with_plan.valuation_ct / 100, 2),
        "bis": plan.slots[-1].start.isoformat() if plan.slots else None,
        "berechnet": computed.isoformat(),
        "rechenzeit_ms": round(runtime_ms),
    }


def planner_facts() -> dict[str, float]:
    """The assumptions the planner calculates with, for the app's explanation (not the author's house)."""
    b = Battery()
    return {
        "pv_akku": b.efficiency_charge,
        "netz_akku": b.efficiency_grid_charge,
        "akku_haus": b.efficiency_discharge,
        "standby_w": round(b.standby_kw * 1000),
        "netzladen_max_kw": b.grid_charge_kw,
        "wechsel_ct": SWITCH_CT,
        "sperren_min": MIN_HOLD_SLOTS * 15,
        "last_faktor": LOAD_FACTOR,
        "pv_p10_anteil": P10_SHARE,
        "spitzen_anteil": SPIKE_SHARE,
    }
