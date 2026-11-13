"""What is set at the inverter, classified once. No imports from the integration: used by the executor
(control.inverter_state), the measurements (measurement.action_from) and the backtest export alike, so
the executor's view and the measured action can never disagree."""

from __future__ import annotations

from enum import Enum

SELF_CONSUMPTION = "Self-consumption mode (default)"
FORCED_MODE = "Forced mode"
VPP_MODE = "VPP"
FORCED_STOP = "Stop (default)"
FORCED_CHARGE = "Forced charge"
FORCED_DISCHARGE = "Forced discharge"
"""Option names of the Sungrow Modbus integration
(select.ems_mode, select.battery_forced_charge_discharge)."""
HOLD_MAX_W = 100
"""A discharge limit up to this counts as hold."""
NO_VALUE = (None, "unknown", "unavailable")


class InverterSetting(Enum):
    """The setting at the inverter; the value is the measured action code."""

    NORMAL = "n"
    HOLD = "h"
    """Self-consumption with a discharge limit ≤ HOLD_MAX_W: the battery keeps its charge."""
    CHARGE = "l"
    """Forced charge from the grid."""
    OLD_HOLD = "f"
    """Forced mode + Stop: the hold of the old YAML executor."""
    FORCED_DISCHARGE = "e"
    VPP = "v"
    """A provider (e.g. Tibber) controls the battery."""
    UNREACHABLE = "x"
    """No value from the inverter (integration down, Modbus timeout); same code as "no data"."""
    OTHER = "a"
    """Any other combination."""


def classify(mode: str | None, forced: str | None, discharge_w: float | None) -> InverterSetting:
    if mode in NO_VALUE or forced in NO_VALUE:
        return InverterSetting.UNREACHABLE
    if mode == VPP_MODE:
        return InverterSetting.VPP
    if mode == FORCED_MODE:
        return {
            FORCED_CHARGE: InverterSetting.CHARGE,
            FORCED_STOP: InverterSetting.OLD_HOLD,
            FORCED_DISCHARGE: InverterSetting.FORCED_DISCHARGE,
        }.get(forced, InverterSetting.OTHER)
    if mode == SELF_CONSUMPTION and forced == FORCED_STOP:
        if discharge_w is None:
            return InverterSetting.UNREACHABLE
        if discharge_w > HOLD_MAX_W:
            return InverterSetting.NORMAL
        if discharge_w >= 0:
            return InverterSetting.HOLD
    return InverterSetting.OTHER
