"""Battery physics and cost for one quarter hour and for a whole plan.

The optimizer and the tests both use this. So the optimizer values a plan exactly the way
the tests check it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .model import DT_H, Action, Battery, Slot, Step, Tariff

STANDBY_PV_KW = 0.05
"""Below this PV power the battery supplies the inverter (at night)."""


def step(e: float, slot: Slot, action: Action, battery: Battery, tariff: Tariff) -> Step:
    """Simulates one quarter hour starting from battery content e (kWh, DC).

    Uses gross flows: the import part and the surplus part of the quarter hour, both increased by
    the spike. The surplus charges first, then the battery covers the import (not with HOLD).
    Without a spike one of the two parts is zero."""
    demand = (slot.load_kw - slot.pv_kw) * DT_H  # > 0: import needed, < 0: PV surplus
    discharged = 0.0
    standby = 0.0
    grid_charged = 0.0

    spike = slot.spike_kw * DT_H
    part_import = max(demand, 0.0) + spike
    part_surplus = max(-demand, 0.0) + spike

    if action is Action.GRID_CHARGE:
        # Battery charges at a fixed power, first from surplus, the rest from the grid; it never
        # discharges, so consumption spikes come from the grid
        eta = battery.efficiency_grid_charge
        in_ac = max(min(battery.grid_charge_kw * DT_H, (battery.e_max - e) / eta), 0.0)
        grid_charged = max(in_ac - part_surplus, 0.0)
        e_new = e + in_ac * eta
        grid_import = part_import + grid_charged
        feed_in = max(part_surplus - in_ac, 0.0)
    else:
        eta_c = battery.efficiency_charge
        in_ac = max(min(part_surplus, battery.charge_kw * DT_H, (battery.e_max - e) / eta_c), 0.0)
        e_charged = e + in_ac * eta_c
        out_ac = 0.0
        if action is Action.NORMAL:
            eta_d = battery.efficiency_discharge
            sb = battery.standby_kw * DT_H if slot.pv_kw < STANDBY_PV_KW else 0.0
            available = max((e_charged - battery.e_min - sb) * eta_d, 0.0)
            out_ac = max(min(part_import, available, battery.discharge_kw * DT_H), 0.0)
            if out_ac > 0:
                standby = sb
                discharged = out_ac / eta_d + sb
        e_new = e_charged - discharged
        grid_import = part_import - out_ac
        feed_in = part_surplus - in_ac

    cost = grid_import * slot.price_ct - feed_in * tariff.feed_in_ct + discharged * battery.wear_ct
    return Step(action, e, e_new, grid_import, feed_in, discharged, standby, grid_charged, cost)


def usable_per_kwh(load_kw: float, battery: Battery) -> float:
    """kWh delivered to the house per kWh of battery content at this load without PV (incl. standby)."""
    if load_kw <= 0:
        return 0.0
    return load_kw / (load_kw / battery.efficiency_discharge + battery.standby_kw)


def residual_per_kwh(slots: Sequence[Slot], battery: Battery, tariff: Tariff) -> float:
    """Value (ct) of one kWh of battery content at the end of the horizon.

    Price: cheapest price in the horizon (or tariff.residual_ct). Efficiency: at the mean load of the
    quarter hours without PV, because that is where the remainder will be used later."""
    price = tariff.residual_ct if tariff.residual_ct is not None else min(s.price_ct for s in slots)
    night = [s.load_kw for s in slots if s.pv_kw < STANDBY_PV_KW]
    load = sum(night) / len(night) if night else 0.5
    return max(price * usable_per_kwh(load, battery) - battery.wear_ct, 0.0)


@dataclass(frozen=True)
class Result:
    steps: list[Step]
    cost_ct: float
    """Sum over the quarter hours, without residual value."""
    residual_ct: float

    @property
    def valuation_ct(self) -> float:
        """What the optimizer minimizes: cost minus the value of the remaining content."""
        return self.cost_ct - self.residual_ct

    @property
    def soc(self) -> list[float]:
        """Battery content after each quarter hour (kWh)."""
        return [s.e_after for s in self.steps]


def simulate(
    slots: Sequence[Slot],
    e0: float,
    actions: Sequence[Action] | None,
    battery: Battery,
    tariff: Tariff,
) -> Result:
    """Runs a plan. actions=None means always NORMAL (battery without control)."""
    if actions is None:
        actions = [Action.NORMAL] * len(slots)
    if len(actions) != len(slots):
        raise ValueError(f"{len(actions)} actions for {len(slots)} quarter hours")
    e = e0
    steps = []
    for slot, action in zip(slots, actions, strict=True):
        s = step(e, slot, action, battery, tariff)
        steps.append(s)
        e = s.e_after
    residual = max(e - battery.e_min, 0.0) * residual_per_kwh(slots, battery, tariff) if slots else 0.0
    return Result(steps, sum(s.cost_ct for s in steps), residual)


def step_vector(
    e: np.ndarray, slot: Slot, action: Action, battery: Battery, tariff: Tariff
) -> tuple[np.ndarray, np.ndarray]:
    """Like `step`, but for many battery contents at once. Returns (content after, cost ct).

    Must compute exactly the same as `step` (checked in test_simulation.py)."""
    demand = (slot.load_kw - slot.pv_kw) * DT_H
    spike = slot.spike_kw * DT_H
    part_import = max(demand, 0.0) + spike
    part_surplus = max(-demand, 0.0) + spike
    if action is Action.GRID_CHARGE:
        eta = battery.efficiency_grid_charge
        in_ac = np.maximum(np.minimum(battery.grid_charge_kw * DT_H, (battery.e_max - e) / eta), 0.0)
        from_grid = np.maximum(in_ac - part_surplus, 0.0)
        e_new = e + in_ac * eta
        cost = (part_import + from_grid) * slot.price_ct - np.maximum(
            part_surplus - in_ac, 0.0
        ) * tariff.feed_in_ct
        return e_new, cost
    eta_c = battery.efficiency_charge
    in_ac = np.maximum(
        np.minimum(np.minimum(part_surplus, battery.charge_kw * DT_H), (battery.e_max - e) / eta_c), 0.0
    )
    e_charged = e + in_ac * eta_c
    if action is Action.NORMAL:
        eta_d = battery.efficiency_discharge
        sb = battery.standby_kw * DT_H if slot.pv_kw < STANDBY_PV_KW else 0.0
        available = np.maximum((e_charged - battery.e_min - sb) * eta_d, 0.0)
        out_ac = np.maximum(np.minimum(np.minimum(part_import, available), battery.discharge_kw * DT_H), 0.0)
        discharged = np.where(out_ac > 0, out_ac / eta_d + sb, 0.0)
    else:
        out_ac = np.zeros_like(e)
        discharged = out_ac
    e_new = e_charged - discharged
    cost = (
        (part_import - out_ac) * slot.price_ct
        - (part_surplus - in_ac) * tariff.feed_in_ct
        + discharged * battery.wear_ct
    )
    return e_new, cost


def flows(step_: Step, slot: Slot, battery: Battery) -> dict[str, float]:
    """Energy flows of one quarter hour in kW (average), for display.

    pv_house + battery_house + grid_house = consumption; pv_house + pv_battery + pv_grid = PV;
    grid_battery = from the grid into the battery."""
    eta_in = (
        battery.efficiency_grid_charge if step_.action is Action.GRID_CHARGE else battery.efficiency_charge
    )
    in_ac_kwh = max((step_.e_after - step_.e_before + step_.discharged_kwh) / eta_in, 0.0)
    grid_battery = min(step_.grid_charged_kwh, in_ac_kwh)
    battery_house = max((step_.discharged_kwh - step_.standby_kwh) * battery.efficiency_discharge, 0.0)
    grid_house = max(step_.import_kwh - grid_battery, 0.0)
    load = slot.load_kw * DT_H
    return {
        k: round(v / DT_H, 2)
        for k, v in {
            "pv_house": max(load - battery_house - grid_house, 0.0),
            "battery_house": battery_house,
            "grid_house": grid_house,
            "pv_battery": max(in_ac_kwh - grid_battery, 0.0),
            "pv_grid": step_.feed_in_kwh,
            "grid_battery": grid_battery,
        }.items()
    }
