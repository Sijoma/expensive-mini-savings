"""Optimizer: chooses, per quarter hour, the action with the lowest total cost.

Dynamic programming over battery content × quarter hours:

1. Backward: for each quarter hour t and each battery content e (grid `GRID_KWH`) compute the
   value V_t(e) = lowest cost from t to the end when starting with e. At the end the residual
   value counts negatively. Values between grid points are interpolated linearly.
2. Forward: starting from the real battery content e0, choose per quarter hour the action with
   cost now + V_{t+1}(content after) and continue exactly (no rounding drift).

Extra state: what ran in the previous quarter hour. This enforces the minimum duration (after one
quarter hour of holding only continuing to hold is allowed) and a fee per switch against toggling.
Cost per quarter hour comes from `simulation`, exactly as in the tests.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .model import MIN_HOLD_SLOTS, Action, Battery, Slot, Tariff
from .simulation import residual_per_kwh, step, step_vector

GRID_KWH = 0.05
"""Spacing of the grid points for the battery content."""

SWITCH_CT = 2.0
"""Fee per started action (hold or grid charge). Prevents toggling at nearly equal prices; on a tie
the battery stays normal. Counts only in the optimizer, not in the valuation."""

# States: what ran in the previous quarter hour. HOLD_NEW = held for one quarter hour only,
# then only continuing to hold is allowed (minimum duration 2 quarter hours).
NORMAL, HOLD_NEW, HOLDING, CHARGING = range(4)
STATES = (NORMAL, HOLD_NEW, HOLDING, CHARGING)


def _next_state(state: int, action: Action) -> tuple[int, float] | None:
    """(state after, switch fee) or None if the action is not allowed."""
    if state == HOLD_NEW:
        return (HOLDING, 0.0) if action is Action.HOLD else None
    if action is Action.HOLD:
        return (HOLDING, 0.0) if state == HOLDING else (HOLD_NEW, SWITCH_CT)
    if action is Action.GRID_CHARGE:
        return (CHARGING, 0.0 if state == CHARGING else SWITCH_CT)
    return (NORMAL, 0.0)


def plan_actions(
    slots: Sequence[Slot], e0: float, battery: Battery, tariff: Tariff, **kwargs
) -> list[Action]:
    """Returns one action per quarter hour. Arguments as for `plan_with_value`."""
    return plan_with_value(slots, e0, battery, tariff, **kwargs)[0]


def plan_with_value(
    slots: Sequence[Slot],
    e0: float,
    battery: Battery,
    tariff: Tariff,
    *,
    running: Action | None = None,
    since: int = 2,
    hold_running: bool = False,
) -> tuple[list[Action], list[float]]:
    """Returns per quarter hour the action and the value of one kWh of battery content after it (ct).

    The value is the slope of the value function: how much the cost from the next quarter hour on
    drops if one more kWh is in the battery. It explains the decisions.

    running/since: what currently runs at the inverter and for how many quarter hours. Continuing
    costs no switch fee. A hold running for only 1 quarter hour must continue (minimum duration).
    hold_running=True is the same as running=HOLD."""
    if hold_running:
        running = Action.HOLD
    if MIN_HOLD_SLOTS != 2:
        raise NotImplementedError("the optimizer only supports a minimum hold of 2 quarter hours")
    n = len(slots)
    if n == 0:
        return [], []

    grid_points = np.linspace(
        battery.e_min, battery.e_max, max(round((battery.e_max - battery.e_min) / GRID_KWH), 1) + 1
    )
    residual = residual_per_kwh(slots, battery, tariff)

    # V[t][state] = cost from quarter hour t on; V[n] = minus residual value
    end = -np.maximum(grid_points - battery.e_min, 0.0) * residual
    V: list[dict[int, np.ndarray]] = [{}] * n + [
        dict.fromkeys(STATES, end)
    ]  # a short block is fine at the end
    for t in range(n - 1, -1, -1):
        transition = {a: step_vector(grid_points, slots[t], a, battery, tariff) for a in Action}
        stage = {}
        for z in STATES:
            best = np.full_like(grid_points, np.inf)
            for a, (e_new, cost) in transition.items():
                f = _next_state(z, a)
                if f is None:
                    continue
                total = cost + f[1] + np.interp(e_new, grid_points, V[t + 1][f[0]])
                best = np.minimum(best, total)
            stage[z] = best
        V[t] = stage

    # Forward with the exact content
    actions: list[Action] = []
    values: list[float] = []
    d = GRID_KWH
    e = e0
    z = {
        Action.HOLD: HOLD_NEW if since < MIN_HOLD_SLOTS else HOLDING,
        Action.GRID_CHARGE: CHARGING,
    }.get(running, NORMAL)
    for t in range(n):
        choice = None
        for a in Action:  # NORMAL first: wins on a tie
            f = _next_state(z, a)
            if f is None:
                continue
            s = step(e, slots[t], a, battery, tariff)
            total = s.cost_ct + f[1] + float(np.interp(s.e_after, grid_points, V[t + 1][f[0]]))
            if choice is None or total < choice[0] - 1e-9:
                choice = (total, a, f[0], s.e_after)
        assert choice is not None
        _, a, z, e = choice
        actions.append(a)
        v = V[t + 1][z]
        lo, hi = max(e - d, battery.e_min), min(e + d, battery.e_max)
        values.append(
            float(np.interp(lo, grid_points, v) - np.interp(hi, grid_points, v)) / (hi - lo)
            if hi > lo
            else 0.0
        )
    return actions, values
