"""Fetch real days from the HA history and aggregate them per quarter hour.

    cd ems && uv run python -m backtest.export 2026-10-30 2026-11-08

Reads the HA API via `ssh ha` (read-only, token of the SSH app). HA keeps raw history for about
10 days, so the days are stored as JSON in backtest/data/ (private, not part of the public repo).

Per quarter hour from the raw values (step function, roughly one value every 30 s); field names are
German (data format):
- pv_kw, last_kw: averages (PV = sensor.total_dc_power, i.e. DC side)
- bezug_brutto_kwh, ueberschuss_brutto_kwh: ∫max(load − PV, 0) and ∫max(PV − load, 0).
  The real spike follows as min(both) (see core.forecast)
- akku_entladen_kwh, akku_geladen_kwh: from sensor.battery_power (+ discharging, − charging), DC side
- netz_bezug_kwh, netz_einspeisung_kwh: from sensor.meter_active_power (+ import, − export)
- soc_prozent and preis_ct at the start, aktion = what was really set at the inverter (see
  measurement.action_from)
"""

from __future__ import annotations

import json
import subprocess
import sys
from bisect import bisect_right
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import backtest  # noqa: F401  (puts the core on sys.path)
from ems.measurement import QUARTER, MeasurementWriter, action_from

TZ = ZoneInfo("Europe/Berlin")
DATA = Path(__file__).parent / "data"

LOCAL = Path(__file__).parent / "local.json"
"""Optional, not published: {"price_entity": "sensor.…"} – entity with the current price in €/kWh."""
PRICE_ENTITY = (
    json.loads(LOCAL.read_text()).get("price_entity", "sensor.electricity_price")
    if LOCAL.exists()
    else "sensor.electricity_price"
)

POWER = ["sensor.load_power", "sensor.total_dc_power", "sensor.battery_power", "sensor.meter_active_power"]
STATE_ENTITIES = [
    "sensor.battery_level",
    PRICE_ENTITY,
    "number.battery_max_discharge_power",
    "select.ems_mode",
    "select.battery_forced_charge_discharge",
]


def fetch_history(start: datetime, end: datetime) -> dict[str, list[tuple[datetime, str]]]:
    def iso(t: datetime) -> str:
        return t.isoformat().replace("+", "%2B")

    url = (
        f"http://supervisor/core/api/history/period/{iso(start)}?end_time={iso(end)}"
        f"&filter_entity_id={','.join(POWER + STATE_ENTITIES)}&minimal_response&no_attributes"
    )
    cmd = f'curl -sf -H "Authorization: Bearer $SUPERVISOR_TOKEN" "{url}"'
    raw = subprocess.run(["ssh", "ha", cmd], capture_output=True, text=True, check=True).stdout
    history = {}
    for series in json.loads(raw):
        eid = series[0]["entity_id"]
        history[eid] = [(datetime.fromisoformat(x["last_changed"]), x["state"]) for x in series]
    return history


class StepSeries:
    """A measurement series as a step function: a value holds until the next one."""

    def __init__(self, series: list[tuple[datetime, str]], number: bool = True):
        self.times: list[datetime] = []
        self.values: list = []
        for t, s in series:
            if number:
                try:
                    v = float(s)
                except ValueError:  # unavailable: the last valid value stays in effect
                    if not self.values:
                        continue
                    v = self.values[-1]
            else:
                v = s
            self.times.append(t)
            self.values.append(v)

    def at(self, t: datetime, default=None):
        i = bisect_right(self.times, t) - 1
        return self.values[i] if i >= 0 else default


def action_at(history, t: datetime) -> str:
    """What was really set at the inverter (middle of the quarter hour)."""
    return action_from(
        StepSeries(history.get("select.ems_mode", []), number=False).at(t, ""),
        StepSeries(history.get("select.battery_forced_charge_discharge", []), number=False).at(t, ""),
        StepSeries(history.get("number.battery_max_discharge_power", [])).at(t, None),
    )


NAMES = {
    "sensor.load_power": "last",
    "sensor.total_dc_power": "pv",
    "sensor.battery_power": "akku",
    "sensor.meter_active_power": "netz",
}


def export_day(day: date) -> dict:
    """Uses the same MeasurementWriter as the integration (custom_components/ems/measurement.py)."""
    start = datetime.combine(day, datetime.min.time(), TZ)
    end = start + timedelta(days=1)
    history = fetch_history(start, end)
    soc = StepSeries(history["sensor.battery_level"])
    price = StepSeries(history[PRICE_ENTITY])
    now = datetime.now(TZ)
    cutoff = min(end, now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0))

    def extra_info(writer: MeasurementWriter) -> None:
        t = writer.quarter.start
        p = price.at(t + timedelta(seconds=30), None)
        writer.extra_info(
            soc_percent=soc.at(t, None),
            price_ct=round(p * 100, 2) if p is not None else None,
            action=action_at(history, t + QUARTER / 2),
        )

    writer = MeasurementWriter(start)
    extra_info(writer)
    events = sorted(
        (t, NAMES[eid], value) for eid in NAMES for t, value in history.get(eid, []) if t < cutoff
    )
    slots: list[dict] = []
    for t, name, value in events:
        while t >= writer.quarter.start + QUARTER:
            slots += writer.boundary(writer.quarter.start + QUARTER)
            extra_info(writer)
        writer.value(name, max(t, start), value)
    while writer.quarter.start + QUARTER <= cutoff:
        slots += writer.boundary(writer.quarter.start + QUARTER)
        extra_info(writer)
    for s in slots:
        s.pop("abgedeckt_s", None)
    return {
        "tag": day.isoformat(),
        "quelle": "HA history (raw) via the API, exported with backtest/export.py",
        "messwerte": {e: len(history.get(e, [])) for e in POWER + STATE_ENTITIES},
        "slots": slots,
    }


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    first = date.fromisoformat(argv[0])
    until = date.fromisoformat(argv[1]) if len(argv) > 1 else first
    DATA.mkdir(exist_ok=True)
    day = first
    while day <= until:
        data = export_day(day)
        file_path = DATA / f"{day.isoformat()}.json"
        file_path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        n = data["messwerte"]["sensor.load_power"]
        print(f"{day}: {len(data['slots'])} quarter hours, {n} consumption readings → {file_path.name}")
        day += timedelta(days=1)


if __name__ == "__main__":
    main(sys.argv[1:])
