"""Plan against reality: which plan was valid for a quarter hour."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from custom_components.ems import history

Q = 900_000
T0 = 1_794_265_200_000  # 2026-11-10 00:00 +01:00


def write(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def recording(time: str, first_ms: int, actions: str) -> dict:
    return {
        "zeit": time,
        "slots": [[first_ms + i * Q, 30, 0, 0.5, 0, 50 + i] for i in range(len(actions))],
        "aktionen": actions,
    }


def measured(at: str, price: float, soc: float, action: str, grid: float) -> dict:
    return {
        "start": f"2026-11-10T{at}:00+01:00",
        "preis_ct": price,
        "soc_prozent": soc,
        "aktion": action,
        "netz_bezug_kwh": grid,
    }


def test_latest_plan_before_the_quarter_wins():
    older = recording("2026-11-10T00:00:10+01:00", T0, "hhhh")
    newer = recording("2026-11-10T00:30:10+01:00", T0 + 2 * Q, "nn")
    planned = history.planned_by_quarter([newer, older])
    assert planned[T0] == ("h", 50)
    assert planned[T0 + 2 * Q] == ("n", 50)
    assert planned[T0 + 3 * Q] == ("n", 51)


def test_plan_computed_after_the_quarter_does_not_count():
    late = recording("2026-11-10T01:00:10+01:00", T0, "llll")
    assert T0 not in history.planned_by_quarter([late])


def test_recording_without_soc_column():
    old = {"zeit": "2026-11-10T00:00:10+01:00", "slots": [[T0, 30, 0, 0.5, 0]], "aktionen": "h"}
    assert history.planned_by_quarter([old])[T0] == ("h", None)


def test_day_combines_measurement_and_plan(tmp_path):
    write(
        tmp_path / "ems" / "messung" / "2026-11-10.jsonl",
        [
            {
                "start": "2026-11-10T00:15:00+01:00",
                "preis_ct": 31,
                "soc_prozent": 49,
                "aktion": "f",
                "netz_bezug_kwh": 0.2,
            },
            {
                "start": "2026-11-10T00:00:00+01:00",
                "preis_ct": 30,
                "soc_prozent": 50,
                "aktion": "h",
                "netz_bezug_kwh": 0.1,
            },
        ],
    )
    write(
        tmp_path / "ems" / "aufzeichnung" / "2026-11-09.jsonl",
        [recording("2026-11-09T23:00:10+01:00", T0 - 4 * Q, "nnnnhh")],
    )
    (tmp_path / "ems" / "aufzeichnung" / "2026-11-10.jsonl").write_text("{broken\n")
    result = history.day(tmp_path, date(2026, 11, 10))
    rows = result["viertelstunden"]
    assert [r["start"] for r in rows] == [T0, T0 + Q]
    assert rows[0]["aktion"] == "h" and rows[0]["plan_aktion"] == "h" and rows[0]["plan_soc"] == 54
    assert rows[1]["soc"] == 49 and rows[1]["bezug_kwh"] == 0.2
    assert result["plaene"] == 1
    assert history.available_days(tmp_path) == ["2026-11-10"]


def test_day_without_data(tmp_path):
    result = history.day(tmp_path, date(2026, 11, 10))
    assert result["viertelstunden"] == [] and history.available_days(tmp_path) == []
