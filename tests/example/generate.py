"""Generates tests/example/ha_eingaben.json: made-up HA states in the format of the real inputs.

    python tests/example/generate.py

No real measurements: prices, PV and consumption are smooth curves. The timestamps (2026-11-08 00:00
to 2026-11-09 23:45) match the tests (planning from 2026-11-08 22:31).
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")
START = datetime(2026, 11, 8, tzinfo=TZ)


def price_ct(t: datetime) -> float:
    """Cheap at night, expensive in the morning and evening, in between at noon."""
    h = t.hour + t.minute / 60
    return round(
        32 + 14 * math.exp(-((h - 7.5) ** 2) / 2) + 34 * math.exp(-((h - 18.5) ** 2) / 4) + 8 * (6 < h < 22),
        2,
    )


def pv_watts(t: datetime) -> float:
    """Glockenkurve um 12:30, ~3,5 kW Spitze."""
    h = t.hour + t.minute / 60
    return round(max(0.0, 3500 * math.exp(-((h - 12.5) ** 2) / 6) - 150), 1)


def main() -> None:
    prices = [
        [
            int((START + timedelta(minutes=15 * i)).timestamp() * 1000),
            price_ct(START + timedelta(minutes=15 * i)),
        ]
        for i in range(192)
    ]
    forecast = []
    for i in range(672):
        t = START + timedelta(minutes=15 * i)
        w = pv_watts(t)
        forecast.append(
            {"datetime": t.isoformat(), "watts": w, "p10": round(w * 0.6, 1), "p90": round(w * 1.2, 1)}
        )
    profile = [
        round(350 + 500 * math.exp(-((h - 7.5) ** 2) / 2) + 600 * math.exp(-((h - 19) ** 2) / 6))
        for h in range(24)
    ]

    def z(state, **attr):
        return {
            "state": state,
            "attributes": attr,
            "last_changed": "2026-11-08T21:00:00+00:00",
        }

    ha_states = {
        "sensor.electricity_prices": z("192", **{"preise": prices}),
        "sensor.pv_forecast": z("0.0", forecast=forecast, unit_of_measurement="W"),
        "sensor.load_profile": z(str(round(sum(profile) / 24)), **{"profil": profile}),
        "sensor.battery_level": z("20.0", unit_of_measurement="%"),
        "sensor.battery_capacity_high_precision": z("10.13", unit_of_measurement="kWh"),
        "number.battery_reserved_soc_for_backup": z("0.0"),
        "input_number.battery_wear": z("10.0"),
        "input_number.grid_charge_power": z("5.0"),
        "sensor.battery_target": z("Eigenverbrauch"),
        "number.battery_max_discharge_power": z("5900.0", max=5900),
    }
    target_path = Path(__file__).with_name("ha_eingaben.json")
    target_path.write_text(
        json.dumps({"notiz": "made-up example data (tests/example/generate.py)", "zustaende": ha_states})
    )
    print(target_path, target_path.stat().st_size, "Bytes")


if __name__ == "__main__":
    main()
