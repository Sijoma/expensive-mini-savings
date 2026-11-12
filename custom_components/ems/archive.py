"""Permanent, append-only archive of what the executor keeps only briefly in its store.

The store (.storage/ems.steuerung) holds the last entries for the app: 50 log entries, 100 booked actions,
100 forecast days. Every entry is also appended here and never removed, so the whole history stays
available for analysis (and in every HA backup):

    /config/ems/protokoll/YYYY-MM.jsonl   log entries (field "zeit")
    /config/ems/abrechnung/YYYY.jsonl     booked actions (field "ende")
    /config/ems/prognose/YYYY.jsonl       daily forecast of the next 24 h (field "at")
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger(__name__)

LOG = "protokoll"
SAVINGS = "abrechnung"
FORECAST = "prognose"

_TIME_FIELD = {LOG: "zeit", SAVINGS: "ende", FORECAST: "at"}
_PERIOD_LEN = {LOG: 7, SAVINGS: 4, FORECAST: 4}  # YYYY-MM or YYYY from the ISO time
_lock = threading.Lock()


def file_for(folder: Path, kind: str, entry: dict[str, Any]) -> Path:
    stamp = str(entry.get(_TIME_FIELD[kind]) or "")
    period = stamp[: _PERIOD_LEN[kind]] if len(stamp) >= _PERIOD_LEN[kind] else "ohne-datum"
    return folder / kind / f"{period}.jsonl"


def append(folder: Path, kind: str, entries: Iterable[dict[str, Any]]) -> None:
    """Append entries (oldest first) to their files. Blocking: run in the executor."""
    by_file: dict[Path, list[str]] = {}
    for entry in entries:
        line = json.dumps(entry, ensure_ascii=False, separators=(", ", ": "))
        by_file.setdefault(file_for(folder, kind, entry), []).append(line)
    with _lock:
        for path, lines in by_file.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
            except OSError as err:
                _LOGGER.warning("EMS: archive %s not written: %s", path, err)


def read(folder: Path, kind: str) -> list[dict[str, Any]]:
    """All archived entries of one kind, in file order (for tests and tools)."""
    out: list[dict[str, Any]] = []
    for path in sorted((folder / kind).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out
