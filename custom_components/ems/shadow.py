"""Shadow battery: what the control really saves, measured against the same battery without control.

No HA import. The ledger answers one question per quarter hour: what would this quarter hour have cost
with an uncontrolled battery (the shadow), and what did it cost in reality? The sum of the differences is
the money saved so far (`cash_ct`). The two batteries rarely hold the same charge, so the difference in
content (`diff_kwh`, real minus shadow) is carried along and valued with the planner's own cost-to-go
(`position_ct`): energy bought at night and not yet used is neither a loss nor a gain until it is used.

The shadow is not simulated on its own (it would drift away from reality within days). Only the
*difference* to the real battery is kept, and the shadow is rebuilt from the measured SoC every quarter hour:

- Normal quarter hours: both batteries run the same model on the same load and PV. If they behave the same,
  nothing changes; if one of them is empty or full, the model gives the cost and content difference.
  Whatever the model gets wrong in normal operation cancels out.
- Controlled quarter hours (hold, grid charge, anything set by somebody else): reality is what the meters
  say, so everything the control really causes counts, also what the model does not know. The shadow runs
  the model.
- Gaps (HA restart, no price, no SoC): nothing is booked and the difference stays as it was.

The same quantity is promised a day ahead (`promise_ct`), so promise and measurement can be compared.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from .core.explanation import Plan
from .core.forecast import PV_EFFICIENCY
from .core.model import DT_H, Action, Battery, Slot, Tariff
from .core.optimizer import plan_actions
from .core.simulation import simulate, step

MIN_COVERED_S = 840
"""A quarter hour measured for less than this (recording began mid-way) is treated as a gap."""
SAME_KWH = 0.02
"""Below this difference in content the two batteries count as equal."""
DAYS_MAX = 100
MIN_DAY_SLOTS = 92
"""A promise needs the whole day in the plan (92 quarter hours on the short DST day)."""

Ledger = dict[str, Any]


def new_ledger(since: str) -> Ledger:
    return {
        "since": since,
        "cash_ct": 0.0,
        "diff_kwh": 0.0,
        "position_ct": 0.0,
        "pending": None,
        "price_ct": None,
        "day": None,
        "days": [],
    }


def total_ct(ledger: Mapping[str, Any]) -> float:
    """Saved so far: money already saved plus the value of the extra content in the battery."""
    return float(ledger["cash_ct"]) + float(ledger["position_ct"])


def _clamp(e: float, battery: Battery) -> float:
    return min(max(e, battery.e_min), battery.e_max)


def shadow_content(e_real: float, diff_kwh: float, battery: Battery) -> float:
    """Content of the shadow battery when the real one holds e_real."""
    return _clamp(e_real - diff_kwh, battery)


def advance(ledger: Ledger, m: Mapping[str, Any], battery: Battery, tariff: Tariff) -> None:
    """Book one finished quarter hour (a record of measurement.Quarter.as_dict)."""
    start = datetime.fromisoformat(m["start"])
    soc = m.get("soc_prozent")
    e_real = soc / 100 * battery.capacity_kwh if soc is not None else None

    # the quarter hour before was controlled: now the SoC tells where the real battery ended up
    pending = ledger.get("pending")
    ledger["pending"] = None
    if pending:
        follows = datetime.fromisoformat(pending["next"]) == start
        e_after = e_real if follows and e_real is not None else pending["e_estimate"]
        ledger["diff_kwh"] = e_after - pending["e_shadow"]

    price = m.get("preis_ct")
    if price is None:
        price = ledger.get("price_ct")  # recording began after a restart: the last known price
    action = m.get("aktion", "n")
    if e_real is None or price is None or action == "x" or m.get("abgedeckt_s", 900) < MIN_COVERED_S:
        return
    ledger["price_ct"] = price

    e_shadow = shadow_content(e_real, float(ledger["diff_kwh"]), battery)
    spike = min(m["bezug_brutto_kwh"], m["ueberschuss_brutto_kwh"]) / DT_H
    slot = Slot(start, price, m["pv_kw"] * PV_EFFICIENCY, m["last_kw"], spike * PV_EFFICIENCY)
    shadow = step(e_shadow, slot, Action.NORMAL, battery, tariff)
    if action == "n":
        real = step(e_real, slot, Action.NORMAL, battery, tariff)
        ledger["cash_ct"] = float(ledger["cash_ct"]) + shadow.cost_ct - real.cost_ct
        ledger["diff_kwh"] = real.e_after - shadow.e_after
        return
    meter = (
        m["netz_bezug_kwh"] * price
        - m["netz_einspeisung_kwh"] * tariff.feed_in_ct
        + m["akku_entladen_kwh"] * battery.wear_ct
    )
    ledger["cash_ct"] = float(ledger["cash_ct"]) + shadow.cost_ct - meter
    estimate = _clamp(e_real + m["akku_geladen_kwh"] - m["akku_entladen_kwh"], battery)
    ledger["diff_kwh"] = estimate - shadow.e_after
    ledger["pending"] = {
        "next": (start + timedelta(minutes=15)).isoformat(),
        "e_shadow": shadow.e_after,
        "e_estimate": estimate,
    }


def roll_day(ledger: Ledger, day: str) -> None:
    """A quarter hour of `day` (YYYY-MM-DD) is about to be booked: close the day before if it is over."""
    total = total_ct(ledger)
    current = ledger.get("day")
    if current is None:
        ledger["day"] = {"tag": day, "start_ct": total}
    elif day > current["tag"]:
        closed = {"tag": current["tag"], "wert": round((total - current["start_ct"]) / 100, 3)}
        ledger["days"] = [*ledger.get("days", []), closed][-DAYS_MAX:]
        ledger["day"] = {"tag": day, "start_ct": total}


def position_ct(
    slots: Sequence[Slot], e_real: float, diff_kwh: float, plan: Plan, battery: Battery, tariff: Tariff
) -> float:
    """What the difference in content is worth: the planner's cost from now on when it starts with the
    shadow's content, minus its cost with the real content. Not a price per kWh times the difference: the
    first kWh in an empty battery is worth more than the last one in a full battery."""
    e_shadow = shadow_content(e_real, diff_kwh, battery)
    if abs(e_real - e_shadow) < SAME_KWH or not slots:
        return 0.0
    actions = plan_actions(slots, e_shadow, battery, tariff)
    return simulate(slots, e_shadow, actions, battery, tariff).valuation_ct - plan.with_plan.valuation_ct


def promise_ct(plan: Plan, e_shadow: float, battery: Battery, tariff: Tariff, day: date) -> float | None:
    """What the plan expects to save on `day` (local calendar day), in the ledger's terms: money saved in
    that day's quarter hours, plus the extra content at the end of the day, minus the extra content the day
    starts with (both valued with the plan's value of one kWh at that moment). None if the plan does not
    cover the whole day."""
    idx = [i for i, s in enumerate(plan.slots) if s.start.date() == day]
    if len(idx) < MIN_DAY_SLOTS:
        return None
    first, last = idx[0], idx[-1]
    with_plan = plan.with_plan.steps
    without = simulate(plan.slots, e_shadow, None, battery, tariff).steps
    cash = sum(without[i].cost_ct - with_plan[i].cost_ct for i in idx)
    more_at_start = with_plan[first].e_before - without[first].e_before
    more_at_end = with_plan[last].e_after - without[last].e_after
    value_at_start = plan.value_ct[first - 1] if first > 0 else plan.value_ct[0]
    return cash + plan.value_ct[last] * more_at_end - value_at_start * more_at_start
