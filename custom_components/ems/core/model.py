"""Planner data: battery, quarter-hour slots, actions.

Plain Python without Home Assistant, so it runs locally with pytest.
Units: energy kWh, power kW, prices ct/kWh, time step 15 min.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

DT_H = 0.25
"""Length of a quarter hour in hours."""


class Action(StrEnum):
    """What the battery does in a quarter hour. The codes are part of the HA interface."""

    NORMAL = "n"
    """Self-consumption: charge from PV surplus, cover demand from the battery."""
    HOLD = "h"
    """Discharge blocked (discharge power ≈ 0). PV surplus still charges the battery."""
    GRID_CHARGE = "l"
    """Charge at a fixed power, from PV and grid."""


@dataclass(frozen=True)
class Battery:
    """Battery and inverter. All power and energy outside the battery is on the AC side.

    Efficiencies and standby were measured on 2026-10-30 to 2026-11-08 (backtest data, see README):
    battery content (DC) and SoC agree within ±2 %, the losses occur in the inverter."""

    capacity_kwh: float = 10.13
    soc_min: float = 0.0
    """Lower bound on the scale of sensor.battery_level. That scale already is the usable range
    (0 % = the inverter's min SoC, 100 % = max SoC), so the lower bound is the backup reserve
    (number.battery_reserved_soc_for_backup, 0 % since 2026-11-02)."""
    soc_max: float = 1.0
    efficiency_charge: float = 0.98
    """PV surplus (AC equivalent) → battery. Measured 0.980 (median 0.982)."""
    efficiency_grid_charge: float = 0.961
    """Grid (AC) → battery. Measured 0.961 while grid charging."""
    efficiency_discharge: float = 0.975
    """Battery → house (AC). Measured 0.975."""
    standby_kw: float = 0.098
    """Inverter self-consumption while the battery discharges without PV. Measured 98 W.
    When the battery is held or empty it does not come from the grid (import = consumption)."""
    wear_ct: float = 10.0
    """Cost per kWh taken out of the battery."""
    grid_charge_kw: float = 5.0
    discharge_kw: float = 5.9
    """SBH100: 118.8 V × 50 A ≈ 5.94 kW (conservative value from the datasheet)."""
    charge_kw: float = 5.9

    @property
    def e_min(self) -> float:
        return self.capacity_kwh * self.soc_min

    @property
    def e_max(self) -> float:
        return self.capacity_kwh * self.soc_max


BATTERY_OLD_PLANNER = Battery(
    soc_min=0.05,  # the old planner used the min SoC (5 %) as lower bound
    efficiency_charge=0.85**0.5,
    efficiency_grid_charge=0.85**0.5,
    efficiency_discharge=0.85**0.5,
    standby_kw=0.0,
)
"""Assumptions of the old Jinja planner (85 % round trip, square root per direction), for cross-checks."""


@dataclass(frozen=True)
class Slot:
    """One quarter hour with price, PV forecast and expected consumption (averages)."""

    start: datetime
    price_ct: float
    pv_kw: float
    load_kw: float
    spike_kw: float = 0.0
    """Power that additionally flows back and forth within the quarter hour: briefly more
    consumption than PV (heat pump, stove, cloud) and briefly more PV than consumption. It cancels
    out on average. With NORMAL it passes through the battery (losses, wear), with HOLD it comes
    from the grid and the surplus still charges. See `forecast.with_spikes`."""


@dataclass(frozen=True)
class Tariff:
    feed_in_ct: float = 7.0
    """Feed-in compensation, about 7 ct."""
    residual_ct: float | None = None
    """Value of a kWh still in the battery at the end of the horizon (before losses).
    None = cheapest price in the horizon. Without a residual value every plan would empty the battery."""


@dataclass(frozen=True)
class Step:
    """Result of one simulated quarter hour."""

    action: Action
    e_before: float
    e_after: float
    import_kwh: float
    feed_in_kwh: float
    discharged_kwh: float
    """Taken out of the battery (decrease of content, including standby)."""
    standby_kwh: float
    """Of which inverter self-consumption."""
    grid_charged_kwh: float
    """Sent from the grid into the battery (before losses)."""
    cost_ct: float


MIN_HOLD_SLOTS = 2
"""Hold for at least 30 min in a row, to avoid toggling."""


def executable(actions: list[Action], min_hold: int = MIN_HOLD_SLOTS) -> bool:
    """Checks the minimum duration: every hold block has at least min_hold quarter hours.
    A block at the end of the horizon may be shorter (it continues in the next plan)."""
    run = 0
    for a in actions:
        if a is Action.HOLD:
            run += 1
            continue
        if 0 < run < min_hold:
            return False
        run = 0
    return True
