"""Adopt complete days from the recording."""

from __future__ import annotations

import json

from backtest import fetch


def day(n=96, covered=900):
    return [{"start": f"2026-11-10T00:{i:02d}", "abgedeckt_s": covered, "last_kw": 1.0} for i in range(n)]


def test_only_complete_days(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch, "HA", tmp_path / "ha")
    monkeypatch.setattr(fetch, "DATA", tmp_path / "data")
    (tmp_path / "ha" / "messung").mkdir(parents=True)
    (tmp_path / "data").mkdir()
    for name, slots in [("2026-11-10", day()), ("2026-11-11", day(50)), ("2026-11-12", day(96, 300))]:
        (tmp_path / "ha" / "messung" / f"{name}.jsonl").write_text("\n".join(json.dumps(s) for s in slots))
    assert fetch.adopt() == ["2026-11-10"]
    data = json.loads((tmp_path / "data" / "2026-11-10.json").read_text())
    assert len(data["slots"]) == 96 and "abgedeckt_s" not in data["slots"][0]
    assert fetch.adopt() == []  # existing days stay untouched
