"""Backtest: let the optimizer replan every 15 min and execute against the real measurements.

    cd ems && uv run python -m backtest.run                  # all stored days
    uv run python -m backtest.run --share 0.1 --wear 5

Forecast modes (what the optimizer knows):
- perfect: real consumption, real PV, real spikes. Upper bound, not achievable.
- profile: consumption = hourly profile of the other days × factor (like the live EMS), PV real
           (historical PV forecasts are not available yet), spikes = share × min(PV, consumption).
- none:    never control (baseline).

Execution is always against reality: real consumption, real PV, real spikes.
Prices are known as with Tibber: before 13:00 until midnight, afterwards until the end of tomorrow.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from backtest.measurement import Measurement, all_days, load_day
from ems.core.forecast import SPIKE_SHARE
from ems.core.model import Action, Battery, Slot, Step, Tariff
from ems.core.optimizer import plan_actions
from ems.core.simulation import residual_per_kwh, step

NEW_PRICES_AT = 13
LOAD_FACTOR = 1.1


@dataclass(frozen=True)
class Run:
    name: str
    steps: list[Step]
    times: list[datetime]
    residual_ct: float

    @property
    def cost_ct(self) -> float:
        return sum(s.cost_ct for s in self.steps)

    @property
    def valuation_ct(self) -> float:
        return self.cost_ct - self.residual_ct

    def per_day(self) -> dict[str, float]:
        total_sum: dict[str, float] = defaultdict(float)
        for t, s in zip(self.times, self.steps, strict=True):
            total_sum[t.date().isoformat()] += s.cost_ct
        return dict(total_sum)

    def actions(self) -> str:
        return "".join(s.action for s in self.steps)


def load_profile(measurements: list[Measurement], exclude_day: str | None = None) -> list[float]:
    """Mean consumption per hour (kW), excluding the day itself (else the forecast would know too much)."""
    total_sum, count = [0.0] * 24, [0] * 24
    for m in measurements:
        if m.start.date().isoformat() == exclude_day:
            continue
        total_sum[m.start.hour] += m.load_kw
        count[m.start.hour] += 1
    return [total_sum[h] / count[h] if count[h] else 0.5 for h in range(24)]


def horizon_end(t: datetime, last_start: datetime) -> datetime:
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day + timedelta(days=1 if t.hour < NEW_PRICES_AT else 2)
    return min(end, last_start + timedelta(minutes=15))


def forecast(
    horizon: list[Measurement], mode: str, profile: list[float], share: float, factor: float
) -> list[Slot]:
    if mode == "perfect":
        return [m.as_slot() for m in horizon]
    slots = []
    for m in horizon:
        real = m.as_slot()
        load = profile[m.start.hour] * factor
        slots.append(replace(real, load_kw=load, spike_kw=share * min(real.pv_kw, load)))
    return slots


def backtest(
    measurements: list[Measurement],
    mode: str,
    battery: Battery,
    tariff: Tariff,
    share: float = SPIKE_SHARE,
    factor: float = LOAD_FACTOR,
) -> Run:
    """mode: 'none' (never control), 'perfect' or 'profile'."""
    e = measurements[0].soc_percent / 100 * battery.capacity_kwh
    profiles: dict[str, list[float]] = {}
    steps, times = [], []
    running, since = None, 0
    for i, m in enumerate(measurements):
        if mode == "none":
            action = Action.NORMAL
        else:
            end = horizon_end(m.start, measurements[-1].start)
            horizon = [x for x in measurements[i:] if x.start < end]
            day = m.start.date().isoformat()
            if day not in profiles:
                profiles[day] = load_profile(measurements, exclude_day=day)
            slots = forecast(horizon, mode, profiles[day], share, factor)
            action = plan_actions(slots, e, battery, tariff, running=running, since=since)[0]
        s = step(e, m.as_slot(), action, battery, tariff)
        steps.append(s)
        times.append(m.start)
        e = s.e_after
        if action is Action.NORMAL:
            running, since = None, 0
        elif action is running:
            since += 1
        else:
            running, since = action, 1
    last_day = [x.as_slot() for x in measurements if x.start.date() == measurements[-1].start.date()]
    residual = max(e - battery.e_min, 0.0) * residual_per_kwh(last_day, battery, tariff)
    return Run(mode, steps, times, residual)


def load(days: list[str]) -> list[Measurement]:
    return [m for day in days for m in load_day(day)]


def report(runs: list[Run], battery: Battery) -> str:
    without = runs[0]
    lines = []
    header = (
        f"{'day':10} "
        + " ".join(f"{run.name:>9}" for run in runs)
        + "   saved (€) "
        + " ".join(f"{run.name:>8}" for run in runs[1:])
    )
    lines.append(header)
    days = without.per_day()
    for day in days:
        values = [run.per_day()[day] for run in runs]
        lines.append(
            f"{day:10} "
            + " ".join(f"{w / 100:8.2f}€" for w in values)
            + "               "
            + " ".join(f"{(values[0] - w) / 100:+8.2f}" for w in values[1:])
        )
    lines.append("")
    for run in runs:
        a = run.actions()
        end = run.steps[-1].e_after / battery.capacity_kwh * 100
        lines.append(
            f"{run.name:8}: cost {run.cost_ct / 100:6.2f} €, residual {run.residual_ct / 100:5.2f} €, "
            f"valuation {run.valuation_ct / 100:6.2f} € "
            f"(saved {(without.valuation_ct - run.valuation_ct) / 100:+.2f} €), "
            f"held {a.count('h') / 4:4.1f} h, grid charging {a.count('l') / 4:4.1f} h, "
            f"switches {sum(1 for x, y in zip('n' + a, a, strict=False) if y != 'n' and x != y)}, "
            f"SoC at the end {end:.0f} %"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", nargs="*", help="YYYY-MM-DD (default: all stored, contiguous)")
    ap.add_argument("--share", type=float, default=SPIKE_SHARE, help="spike share in the forecast")
    ap.add_argument("--factor", type=float, default=LOAD_FACTOR, help="factor on the load profile")
    ap.add_argument("--wear", type=float, default=None, help="wear in ct per kWh taken from the battery")
    ap.add_argument("--modes", nargs="*", default=["profile", "perfect"])
    args = ap.parse_args()
    battery = Battery() if args.wear is None else Battery(wear_ct=args.wear)
    tariff = Tariff()
    measurements = load(args.days or all_days())
    runs = [
        backtest(measurements, m, battery, tariff, args.share, args.factor) for m in ["none", *args.modes]
    ]
    print(report(runs, battery))


if __name__ == "__main__":
    main()
