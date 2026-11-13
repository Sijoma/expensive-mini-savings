"""Fetch the integration's recordings from HA and adopt complete days for the backtest.

    cd ems && uv run python -m backtest.fetch

1. rsync ha:/homeassistant/ems/ → backtest/ha/ (messung/*.jsonl and aufzeichnung/*.jsonl, read-only)
2. Every day in messung/ that is complete and missing in data/ is stored as data/YYYY-MM-DD.json
   (same format as backtest/export.py).
The data stays on HA as well (and is part of the HA backups).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
HA = Path(os.environ.get("EMS_MIRROR", HERE / "ha"))
"""Mirror of ha:/homeassistant/ems/; EMS_MIRROR points adopt() at another copy (the private ems-daten/ems)."""
DATA = HERE / "data"


def fetch() -> None:
    HA.mkdir(exist_ok=True)
    subprocess.run(["rsync", "-rtz", "ha:/homeassistant/ems/", f"{HA}/"], check=True)


def complete(slots: list[dict]) -> bool:
    """A day has 92, 96 or 100 quarter hours, each fully measured."""
    return len(slots) in (92, 96, 100) and all(s.get("abgedeckt_s") == 900 for s in slots)


def adopt() -> list[str]:
    new = []
    for file_path in sorted((HA / "messung").glob("*.jsonl")):
        day = file_path.stem
        target_path = DATA / f"{day}.json"
        if target_path.exists():
            continue
        slots = [json.loads(z) for z in file_path.read_text().splitlines() if z.strip()]
        if not complete(slots):
            continue
        for s in slots:
            s.pop("abgedeckt_s", None)
        target_path.write_text(
            json.dumps(
                {"tag": day, "quelle": "recording of the integration (ems/messung)", "slots": slots},
                indent=1,
                ensure_ascii=False,
            )
            + "\n"
        )
        new.append(day)
    return new


def main() -> None:
    fetch()
    new = adopt()
    print("new days:", ", ".join(new) if new else "none")


if __name__ == "__main__":
    main()
