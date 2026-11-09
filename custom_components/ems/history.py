"""Plan against reality for one day: measured quarter hours next to what the plan wanted at the time.
No HA import, so it is testable. Used by the app (websocket command ems/history)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

QUARTER_MS = 900_000


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Lines of a JSONL file; broken lines (e.g. cut off by a restart) are skipped."""
    if not path.is_file():
        return []
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return lines


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def planned_by_quarter(recordings: list[dict[str, Any]]) -> dict[int, tuple[str, float | None]]:
    """Quarter start (ms) → (action, SoC %) from the latest plan computed before that quarter."""
    result: dict[int, tuple[str, float | None, int]] = {}
    for rec in recordings:
        try:
            computed = _ms(rec["zeit"])
            actions = rec["aktionen"]
            slots = rec["slots"]
        except (KeyError, TypeError, ValueError):
            continue
        for slot, action in zip(slots, actions, strict=False):
            start = int(slot[0])
            if start + QUARTER_MS <= computed:
                continue  # plan was computed after the quarter ended
            soc = slot[5] if len(slot) > 5 else None
            known = result.get(start)
            if known is None or computed > known[2]:
                result[start] = (action, soc, computed)
    return {k: (v[0], v[1]) for k, v in result.items()}


def day(config_dir: Path, day_: date) -> dict[str, Any]:
    """Measurements of a day with the planned action per quarter hour."""
    measured = read_jsonl(config_dir / "ems" / "messung" / f"{day_:%Y-%m-%d}.jsonl")
    recordings = [
        *read_jsonl(config_dir / "ems" / "aufzeichnung" / f"{day_ - timedelta(days=1):%Y-%m-%d}.jsonl")[-1:],
        *read_jsonl(config_dir / "ems" / "aufzeichnung" / f"{day_:%Y-%m-%d}.jsonl"),
    ]
    planned = planned_by_quarter(recordings)
    rows = []
    for m in measured:
        try:
            start = _ms(m["start"])
        except (KeyError, TypeError, ValueError):
            continue
        plan_action, plan_soc = planned.get(start, (None, None))
        rows.append(
            {
                "start": start,
                "preis_ct": m.get("preis_ct"),
                "soc": m.get("soc_prozent"),
                "aktion": m.get("aktion"),
                "plan_aktion": plan_action,
                "plan_soc": plan_soc,
                "pv_kw": m.get("pv_kw"),
                "last_kw": m.get("last_kw"),
                "bezug_kwh": m.get("netz_bezug_kwh"),
                "einspeisung_kwh": m.get("netz_einspeisung_kwh"),
            }
        )
    rows.sort(key=lambda r: r["start"])
    return {"tag": f"{day_:%Y-%m-%d}", "viertelstunden": rows, "plaene": len(recordings)}


def available_days(config_dir: Path) -> list[str]:
    folder = config_dir / "ems" / "messung"
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.jsonl"))
