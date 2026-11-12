"""Summarize a plan: action blocks with savings and a plain-text reason.

The texts shown to the user (state names, reasons) are German: they are part of the HA interface
of the author's setup."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

from .heat_pump import plan_boost
from .model import DT_H, MIN_HOLD_SLOTS, Action, Battery, Slot, Tariff
from .optimizer import plan_with_value
from .simulation import STANDBY_PV_KW, Result, simulate, usable_per_kwh

# State names of the plan sensor (HA interface, German)
NAMES = {
    Action.NORMAL: "Eigenverbrauch",
    Action.HOLD: "Entladung gesperrt",
    Action.GRID_CHARGE: "Laden aus dem Netz",
}


@dataclass(frozen=True)
class Block:
    first: int
    """Index of the first quarter hour."""
    until: int
    """Index after the last quarter hour."""
    action: Action
    price_ct: float
    """Mean price in the block."""
    value_ct: float
    """Mean value of one kWh in the battery (ct per kWh of content) if it is kept."""
    now_ct: float
    """What the same kWh would yield if the battery delivered it now (ct per kWh of content)."""
    grid_kwh: float
    """Hold: energy taken from the grid instead of the battery. Grid charge: charged from the grid."""
    saves_ct: float
    """Compared with the same plan without this block (recomputed)."""
    reason: str
    """Plain sentence: what the block is for."""
    calculation: str = ""
    """The arithmetic behind it (value of one kWh later against now, or savings after losses and wear)."""


@dataclass(frozen=True)
class Plan:
    slots: list[Slot]
    actions: list[Action]
    value_ct: list[float]
    with_plan: Result
    without: Result
    blocks: list[Block]
    boost: list[bool]
    """Heat pump: PV boost per quarter hour."""

    @property
    def saves_ct(self) -> float:
        return self.without.valuation_ct - self.with_plan.valuation_ct

    @property
    def boost_now(self) -> bool:
        return bool(self.boost and self.boost[0])

    @property
    def now(self) -> Action:
        return self.actions[0] if self.actions else Action.NORMAL

    @property
    def reason_now(self) -> str:
        for b in self.blocks:
            if b.first == 0:
                return b.reason
        return "Akku läuft normal"


def value_now(slot: Slot, battery: Battery) -> float:
    """Value of one kWh of battery content if it is delivered in this quarter hour."""
    residual = slot.load_kw - slot.pv_kw
    if slot.pv_kw < STANDBY_PV_KW:
        usable = usable_per_kwh(max(residual, 0.05), battery)
    else:
        usable = battery.efficiency_discharge
    return slot.price_ct * usable - battery.wear_ct


def _blocks(actions: Sequence[Action]) -> list[tuple[int, int, Action]]:
    out, start = [], 0
    for i in range(1, len(actions) + 1):
        if i == len(actions) or actions[i] is not actions[start]:
            if actions[start] is not Action.NORMAL:
                out.append((start, i, actions[start]))
            start = i
    return out


def _used_when(slots, with_plan: Result, without_block: Result, after: int):
    """When the kept or charged energy is used: quarter hours from `after` on in which the battery
    delivers more with the block than without it, plus the price weighted by that. None if none."""
    extra = [
        (j, with_plan.steps[j].discharged_kwh - without_block.steps[j].discharged_kwh)
        for j in range(after, len(slots))
    ]
    extra = [(j, m) for j, m in extra if m > 0.005]
    if not extra:
        return None
    amount = sum(m for _, m in extra)
    price = sum(m * slots[j].price_ct for j, m in extra) / amount
    return slots[extra[0][0]].start, slots[extra[-1][0]].start + timedelta(minutes=15), price


FULL_EPS_KWH = 1e-6


def hold_when_full(
    slots: Sequence[Slot], e0: float, actions: Sequence[Action], battery: Battery, tariff: Tariff
) -> list[Action]:
    """Grid charging with a full battery charges nothing: the battery just waits, full. Show (and run)
    that part as HOLD, so the plan says what happens. Only runs of at least the minimum hold length
    are changed, and only if the result costs exactly the same; otherwise the actions stay as they are."""
    actions = list(actions)
    steps = simulate(slots, e0, actions, battery, tariff).steps
    idle = [
        a is Action.GRID_CHARGE
        and st.grid_charged_kwh < FULL_EPS_KWH
        and st.e_before >= battery.e_max - FULL_EPS_KWH
        for a, st in zip(actions, steps, strict=True)
    ]
    changed = list(actions)
    i = 0
    while i < len(idle):
        if not idle[i]:
            i += 1
            continue
        j = i
        while j < len(idle) and idle[j]:
            j += 1
        if j - i >= MIN_HOLD_SLOTS:
            changed[i:j] = [Action.HOLD] * (j - i)
        i = j
    if changed == actions:
        return actions
    before = simulate(slots, e0, actions, battery, tariff)
    after = simulate(slots, e0, changed, battery, tariff)
    if abs(before.valuation_ct - after.valuation_ct) > 1e-6:
        return actions
    return changed


def _distinct_ct(a: float, b: float) -> tuple[str, str]:
    """Two ct values for a comparison: whole numbers, or one decimal when rounding would make them look
    equal (11.4 against 11.0 must not read as 11 against 11)."""
    if round(a) != round(b):
        return f"{a:.0f}", f"{b:.0f}"
    return f"{a:.1f}".replace(".", ","), f"{b:.1f}".replace(".", ",")


def create_plan(slots: Sequence[Slot], e0: float, battery: Battery, tariff: Tariff, **kwargs) -> Plan:
    """Plans, recomputes and explains. kwargs as for `optimizer.plan_with_value`."""
    slots = list(slots)
    actions, value = plan_with_value(slots, e0, battery, tariff, **kwargs)
    actions = hold_when_full(slots, e0, actions, battery, tariff)
    with_plan = simulate(slots, e0, actions, battery, tariff)
    without = simulate(slots, e0, None, battery, tariff)
    blocks = []
    for first, until, action in _blocks(actions):
        without_block = list(actions)
        without_block[first:until] = [Action.NORMAL] * (until - first)
        alternative = simulate(slots, e0, without_block, battery, tariff)
        saves = alternative.valuation_ct - with_plan.valuation_ct
        price = sum(s.price_ct for s in slots[first:until]) / (until - first)
        w = sum(value[first:until]) / (until - first)
        now = sum(value_now(s, battery) for s in slots[first:until]) / (until - first)
        steps = with_plan.steps[first:until]
        used = _used_when(slots, with_plan, alternative, until)
        nb = "\u00a0"  # keeps "Ø 45" together
        window = f"für {used[0]:%H:%M}–{used[1]:%H:%M}" if used else "für später"
        worth = (w + battery.wear_ct) / battery.efficiency_discharge
        if used:
            then = f", dann kostet Strom Ø{nb}{used[2]:.0f} ct"
        else:
            then = f", dann ist 1 kWh ≈{nb}{worth:.0f} ct wert"
        if action is Action.HOLD:
            grid = sum(s.import_kwh for s in steps)
            reason = (
                f"Strom kostet in dieser Zeit {price:.0f} ct. Der Akku hebt seine Ladung {window} auf{then}"
            )
            later_ct, now_ct = _distinct_ct(w, now)
            calculation = (
                f"1 kWh aus dem Akku spart später ≈ {later_ct} ct, in dieser Zeit nur ≈ {now_ct} ct "
                "(nach Verlusten und Verschleiß)"
            )
        else:
            grid = sum(s.grid_charged_kwh for s in steps)
            # value of the first charged kWh (before charging): the last one is worth less
            w = max(value[max(first - 1, 0) : until])
            reason = f"Laden zu {price:.0f} ct {window}{then}"
            kwh = f"{grid:.1f}".replace(".", ",")
            eur = f"{saves / 100:.2f}".replace(".", ",")
            calculation = (
                f"{kwh} kWh aus dem Netz, 1 kWh im Akku ist später ≈ {w:.0f} ct wert. "
                f"Nach Verlusten und Verschleiß spart das {eur} €"
            )
        blocks.append(Block(first, until, action, price, w, now, grid, saves, reason, calculation))
    return Plan(slots, actions, value, with_plan, without, blocks, plan_boost(slots, with_plan.steps))


def duration_h(block: Block) -> float:
    return (block.until - block.first) * DT_H
