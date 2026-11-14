"""Aggregate measurements per quarter hour. No HA import: used by the integration (live) and by
backtest/export.py (from history), so both compute exactly the same.

Power values arrive as a step function: a value holds until the next one. Invalid values
(unavailable) change nothing, the last valid value stays in effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any

from .inverter import classify

QUARTER = timedelta(minutes=15)

POWERS = ("last", "pv", "akku", "netz")
"""load, pv (DC), battery (+ discharging / − charging), grid (+ import / − export); all in W."""


def as_number(value: Any, default: float | None = None) -> float | None:
    """The value as a number; `default` if missing, not a number or nan (the one parser of the EMS)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v == v else default


def action_from(mode: str | None, forced: str | None, discharge_max: Any) -> str:
    """What is actually set at the inverter, as action code (inverter.InverterSetting): n normal, h hold,
    l grid charging, f Forced/Stop (old-style hold), e forced discharge, v VPP (a provider controls the
    battery), x no value from the inverter, a any other combination."""
    return classify(mode, forced, as_number(discharge_max)).value


def quarter_start(t: datetime) -> datetime:
    return t.replace(minute=t.minute - t.minute % 15, second=0, microsecond=0)


@dataclass
class Quarter:
    """Sums of one quarter hour in kWh."""

    start: datetime
    since: datetime
    """Since when it was measured (later than start if recording began mid-way)."""
    load: float = 0.0
    pv: float = 0.0
    import_gross: float = 0.0
    surplus_gross: float = 0.0
    discharged: float = 0.0
    charged: float = 0.0
    grid_import: float = 0.0
    grid_export: float = 0.0
    extra_info: dict[str, Any] = field(default_factory=dict)
    """soc_percent, price_ct (at the start), action and plan_action/plan_soc_percent (middle): what was set
    at the inverter and what the plan valid at that moment wanted."""

    def add(self, duration_s: float, w: dict[str, float]) -> None:
        h = duration_s / 3600
        load, pv, battery, grid = (w.get(k, 0.0) / 1000 for k in POWERS)
        self.load += load * h
        self.pv += pv * h
        self.import_gross += max(load - pv, 0.0) * h
        self.surplus_gross += max(pv - load, 0.0) * h
        self.discharged += max(battery, 0.0) * h
        self.charged += max(-battery, 0.0) * h
        self.grid_import += max(grid, 0.0) * h
        self.grid_export += max(-grid, 0.0) * h

    def as_dict(self, end: datetime, tz: tzinfo | None = None) -> dict[str, Any]:
        """Same fields as backtest/data/*.json (German keys, data format), plus covered seconds."""
        h = QUARTER.total_seconds() / 3600
        return {
            "start": self.start.astimezone(tz).isoformat(),
            "preis_ct": self.extra_info.get("price_ct"),
            "pv_kw": round(self.pv / h, 3),
            "last_kw": round(self.load / h, 3),
            "bezug_brutto_kwh": round(self.import_gross, 4),
            "ueberschuss_brutto_kwh": round(self.surplus_gross, 4),
            "akku_entladen_kwh": round(self.discharged, 4),
            "akku_geladen_kwh": round(self.charged, 4),
            "netz_bezug_kwh": round(self.grid_import, 4),
            "netz_einspeisung_kwh": round(self.grid_export, 4),
            "soc_prozent": self.extra_info.get("soc_percent"),
            "aktion": self.extra_info.get("action", "n"),
            "plan_aktion": self.extra_info.get("plan_action"),
            "plan_soc_prozent": self.extra_info.get("plan_soc_percent"),
            "abgedeckt_s": round((end - self.since).total_seconds()),
        }


class MeasurementWriter:
    """Takes measurements in time order and returns one record per completed quarter hour."""

    def __init__(self, start: datetime) -> None:
        """start must be timezone-aware. Computes in UTC (quarter hours stay correct across DST
        changes), outputs in the timezone of start."""
        self.tz = start.tzinfo
        start = start.astimezone(UTC)
        self.values: dict[str, float] = {}
        self.t = start
        self.quarter = Quarter(quarter_start(start), start)

    def _advance_to(self, t: datetime) -> None:
        t = t.astimezone(UTC)
        if t > self.t:
            self.quarter.add((t - self.t).total_seconds(), self.values)
            self.t = t

    def value(self, name: str, t: datetime, value: Any) -> list[dict[str, Any]]:
        """New measurement. Returns completed quarter hours if t crosses a boundary."""
        finished = self.boundary(t)
        self._advance_to(t)
        v = as_number(value)
        if v is not None:
            self.values[name] = v
        return finished

    def extra_info(self, **values: Any) -> None:
        self.quarter.extra_info.update({k: v for k, v in values.items() if v is not None})

    def boundary(self, t: datetime) -> list[dict[str, Any]]:
        """Completes all quarter hours that end by t."""
        finished = []
        t = t.astimezone(UTC)
        while t >= self.quarter.start + QUARTER:
            end = self.quarter.start + QUARTER
            self._advance_to(end)
            finished.append(self.quarter.as_dict(end, self.tz))
            self.quarter = Quarter(end, end)
        return finished
