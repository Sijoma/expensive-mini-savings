"""Evaluate the hold test night (automation test_akku_sperre_vergleich): does the inverter draw its
self-consumption from the grid with both kinds of hold, or only with the new one?

    tools/ems_pull.sh                                   # mirror the measurements from HA
    cd ems && uv run python -m backtest.hold_test 2026-11-16      # the evening the test started

Reads ems-daten/ems/messung/ (or $EMS_MIRROR/messung). Per quarter hour between 22:00 and 02:00:
extra grid power = net grid import minus what the house needed beyond PV. The first quarter hour of each
phase is left out (the switch happens a few seconds into it). Phases are told apart by the recorded
action: "h" = new hold (self-consumption, discharge limit 10 W), "f" = old hold (forced mode, stop).
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

MIRROR = Path(os.environ.get("EMS_MIRROR", Path(__file__).parents[2] / "ems-daten" / "ems"))
NAMES = {"h": "new hold (limit 10 W)", "f": "old hold (forced, stop)", "n": "normal"}
MIN_COVERED_S = 880


def night(evening: str) -> list[dict]:
    """Complete quarter hours from 22:00 on `evening` to 02:00 the next day."""
    start = datetime.fromisoformat(f"{evening}T22:00:00")
    days = [evening, (start + timedelta(days=1)).date().isoformat()]
    rows = []
    for day in days:
        file = MIRROR / "messung" / f"{day}.jsonl"
        if file.exists():
            rows += [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines() if line]
    end = start + timedelta(hours=4)
    return [
        r
        for r in rows
        if start <= datetime.fromisoformat(r["start"]).replace(tzinfo=None) < end
        and r.get("abgedeckt_s", 900) >= MIN_COVERED_S
    ]


def extra_grid_w(r: dict) -> float:
    net_grid = r["netz_bezug_kwh"] - r["netz_einspeisung_kwh"]
    needed = r["bezug_brutto_kwh"] - r["ueberschuss_brutto_kwh"]
    return (net_grid - needed) * 4000


def report(evening: str) -> str:
    rows = night(evening)
    if not rows:
        return f"No measurements for the night of {evening} in {MIRROR / 'messung'} (run tools/ems_pull.sh)."
    lines = [f"{'time':5} {'action':6} {'extra grid W':>12} {'battery out W':>13} {'load W':>7} {'SoC %':>6}"]
    by_action: dict[str, list[float]] = {}
    previous = None
    for r in rows:
        first = r["aktion"] != previous
        previous = r["aktion"]
        extra = extra_grid_w(r)
        note = "  (first of phase, left out)" if first else ""
        lines.append(
            f"{r['start'][11:16]} {r['aktion']:6} {extra:12.0f} {r['akku_entladen_kwh'] * 4000:13.0f} "
            f"{r['last_kw'] * 1000:7.0f} {r['soc_prozent']!s:>6}{note}"
        )
        if not first:
            by_action.setdefault(r["aktion"], []).append(extra)
    lines.append("")
    for action, values in by_action.items():
        lines.append(
            f"{NAMES.get(action, action):26} {len(values):2d} quarter hours: extra grid power "
            f"median {statistics.median(values):4.0f} W, mean {statistics.mean(values):4.0f} W, "
            f"range {min(values):.0f} to {max(values):.0f} W"
        )
    soc = [r["soc_prozent"] for r in rows if r["soc_prozent"] is not None]
    if soc:
        lines.append(f"SoC {soc[0]} % → {soc[-1]} % over the night")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(sys.argv[1] if len(sys.argv) > 1 else datetime.now().date().isoformat()))
