"""Preparing the forecast for the optimizer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from .model import Slot

SPIKE_SHARE = 0.25
"""Assumption: a quarter of the power during which PV and consumption coincide flows back and forth
within the quarter hour (or the forecast is off by that much). Measured spikes were 2–11 %; the rest
covers forecast errors. Results are not very sensitive to this value (see backtest)."""


PV_EFFICIENCY = 0.964
"""PV power DC (Helios, sensor.total_dc_power) → AC. Measured 0.964 with the battery idle."""


def pv_to_ac(slots: Sequence[Slot], efficiency: float = PV_EFFICIENCY) -> list[Slot]:
    """Converts DC-side PV values to AC. The optimizer expects AC."""
    return [replace(s, pv_kw=s.pv_kw * efficiency) for s in slots]


def prepare(slots: Sequence[Slot], share: float = SPIKE_SHARE) -> list[Slot]:
    """Convert PV to AC and set spikes: this is how the optimizer receives the forecast."""
    return with_spikes(pv_to_ac(slots), share)


def with_spikes(slots: Sequence[Slot], share: float = SPIKE_SHARE) -> list[Slot]:
    """Sets spike_kw = share × min(PV, consumption). Without PV (at night) there is no spike."""
    return [replace(s, spike_kw=share * min(s.pv_kw, s.load_kw)) for s in slots]
