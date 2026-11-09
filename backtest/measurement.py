"""Load measured days (backtest/data/*.json) and convert them into slots for simulation and optimizer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ems.core.forecast import PV_EFFICIENCY
from ems.core.model import DT_H, Action, Slot

DATA = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Measurement:
    start: datetime
    price_ct: float
    pv_dc_kw: float
    load_kw: float
    import_gross_kwh: float
    surplus_gross_kwh: float
    battery_discharged_kwh: float
    battery_charged_kwh: float
    grid_import_kwh: float
    grid_export_kwh: float
    soc_percent: float | None
    action: str
    """n, h, l wie Aktion; f = alte Sperre (Forced/Stop); e = erzwungenes Entladen; v = VPP (Tibber)."""

    @property
    def foreign_charged(self) -> bool:
        """The battery charged more than the PV surplus without HA showing grid charging
        (e.g. a schedule in the inverter app or VPP)."""
        return self.action != "l" and self.battery_charged_kwh > self.surplus_gross_kwh + 0.05

    @property
    def foreign_held(self) -> bool:
        """The battery barely discharges at night although HA shows normal and it is not empty."""
        return (
            self.action == "n"
            and self.pv_dc_kw < 0.05
            and self.load_kw > 0.3
            and self.soc_percent is not None
            and self.soc_percent > 10
            and self.battery_discharged_kwh < 0.5 * self.load_kw * DT_H
        )

    @property
    def foreign(self) -> bool:
        """The inverter did something HA does not show. Skip it when comparing with reality."""
        return self.foreign_charged or self.foreign_held or self.ems_action is None

    def as_slot(self, price_ct: float | None = None) -> Slot:
        """Reality as a slot: PV converted to AC, spike from the gross flows."""
        spike = min(self.import_gross_kwh, self.surplus_gross_kwh) / DT_H
        return Slot(
            self.start,
            self.price_ct if price_ct is None else price_ct,
            self.pv_dc_kw * PV_EFFICIENCY,
            self.load_kw,
            spike * PV_EFFICIENCY,
        )

    @property
    def ems_action(self) -> Action | None:
        """Entsprechende Aktion des EMS, None wenn es keine gibt (f, e)."""
        return Action(self.action) if self.action in ("n", "h", "l") else None


def load_day(day: str) -> list[Measurement]:
    raw = json.loads((DATA / f"{day}.json").read_text())
    return [
        Measurement(
            datetime.fromisoformat(s["start"]),
            s["preis_ct"],
            s["pv_kw"],
            s["last_kw"],
            s["bezug_brutto_kwh"],
            s["ueberschuss_brutto_kwh"],
            s["akku_entladen_kwh"],
            s["akku_geladen_kwh"],
            s["netz_bezug_kwh"],
            s["netz_einspeisung_kwh"],
            s["soc_prozent"],
            s["aktion"],
        )
        for s in raw["slots"]
    ]


def all_days() -> list[str]:
    return sorted(p.stem for p in DATA.glob("*.json"))
