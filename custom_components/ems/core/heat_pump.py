"""Heat pump: plan the PV boost (SG Ready input 1 = boosted operation).

Rules: boost only with PV surplus that would otherwise be exported according to the battery plan,
never with grid or battery energy. Of the expected export of a contiguous surplus phase, 80 % is
planned, with 1 kW extra consumption per boost quarter hour (conservative). The sunniest quarter
hours (largest surplus) first.
"""

from __future__ import annotations

from collections.abc import Sequence

from .model import DT_H, Slot, Step

HP_KW = 1.0
"""Extra consumption of the heat pump while boosting (conservative assumption)."""
HP_SHARE = 0.8
"""Plan only this share of the expected export (forecast uncertainty)."""


def plan_boost(
    slots: Sequence[Slot], steps: Sequence[Step], kw: float = HP_KW, share: float = HP_SHARE
) -> list[bool]:
    """True = PV boost in this quarter hour."""
    boost = [False] * len(slots)
    phase: list[int] = []
    for i in range(len(slots) + 1):
        if i < len(slots) and slots[i].pv_kw > slots[i].load_kw:
            phase.append(i)
            continue
        if phase:
            feed_in = sum(steps[j].feed_in_kwh for j in phase)
            count = int(feed_in * share / (kw * DT_H))
            sunny = sorted(phase, key=lambda j: slots[j].load_kw - slots[j].pv_kw)[:count]
            for j in sunny:
                boost[j] = True
            phase = []
    return boost
