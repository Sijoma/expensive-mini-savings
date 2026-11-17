"""Rules that must hold for any input (Hypothesis), for the pure modules of the integration:
inverter classification, approvals and target, booking, load profile, switching decisions, prices.

`uv run pytest --hypothesis-profile=thorough` runs 2000 examples per rule (before a release)."""

from __future__ import annotations

import itertools
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given
from hypothesis import strategies as st

from custom_components.ems import control as c
from custom_components.ems import switching as sw
from custom_components.ems.adapter import PlanBlock, price_at, prices_from_tibber
from custom_components.ems.const import GRID_KW_MAX
from custom_components.ems.measurement import action_from, as_number

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=TZ)
NOW_MS = int(NOW.timestamp() * 1000)
QUARTER = 15 * 60_000

KNOWN = [c.SELF_CONSUMPTION, c.FORCED_MODE, "VPP", c.FORCED_STOP, c.FORCED_CHARGE, "Forced discharge"]
option = st.one_of(st.none(), st.text(max_size=12), st.sampled_from([*KNOWN, "unavailable", "unknown"]))
raw_number = st.one_of(
    st.none(), st.floats(allow_nan=True, allow_infinity=True), st.text(max_size=6), st.integers()
)

blocks = st.lists(
    st.builds(
        lambda code, start, length, kwh, saves: PlanBlock(
            start_ms=NOW_MS + start * QUARTER,
            end_ms=NOW_MS + (start + length) * QUARTER,
            code=code,
            kwh=kwh if code == "l" else 0.0,
            saves_eur=saves,
        ),
        st.sampled_from(["h", "l"]),
        st.integers(-8, 120),
        st.integers(1, 16),
        st.floats(0, 20),
        st.one_of(st.none(), st.floats(0, 3)),
    ),
    max_size=8,
)


# ---- inverter ----


@given(option, option, raw_number)
def test_inverter_views_never_fail_and_agree(mode, forced, discharge):
    code = action_from(mode, forced, discharge)
    state = c.inverter_state(mode, forced, as_number(discharge))
    assert (code == "n") == (state == c.NORMAL)
    assert (code == "h") == (state == c.HOLD)
    assert (code == "l") == (state == c.CHARGE)
    assert (code == "x") == (state == c.UNREACHABLE)
    assert (code == "f") == (state == c.HOLD_NO_PV)


@given(st.one_of(raw_number, st.lists(st.integers()), st.dictionaries(st.text(), st.integers())))
def test_as_number_never_raises_and_never_returns_nan(value):
    result = as_number(value)
    assert result is None or result == result


# ---- target and approvals ----


@given(blocks, st.sampled_from(c.MODES), st.floats(0, 100), st.sampled_from([c.NORMAL, c.HOLD, c.CHARGE]))
def test_target_rules(items, mode, soc, active):
    assert c.target(mode, items, NOW_MS, False, c.Approval(), active, soc) == c.NORMAL  # no fresh plan
    target = c.target(mode, items, NOW_MS, True, c.Approval(), active, soc)
    if mode in (c.PLAN_ONLY, c.WITH_APPROVAL):
        assert target == c.NORMAL  # plan only, or nothing approved
    if soc >= c.CHARGE_STOP_SOC:
        assert target != c.CHARGE
    assert c.target(mode, items, NOW_MS, True, c.Approval(), active, None) != c.CHARGE  # SoC unknown


@given(blocks, st.floats(0, 100))
def test_nothing_runs_in_approval_mode_that_was_not_approved(items, soc):
    approved = c.approve(items, NOW, c.Approval())
    target = c.target(c.WITH_APPROVAL, items, NOW_MS, True, approved, c.NORMAL, soc)
    if target != c.NORMAL:
        assert (
            c.CODE[target] in approved.codes or target == c.HOLD
        )  # a full battery holds instead of charging


@given(blocks)
def test_approve_or_reject_settles_what_was_offered(items):
    assert not c.pending(c.WITH_APPROVAL, items, NOW_MS, c.reject(items, NOW, c.Approval()))
    approved = c.approve(items, NOW, c.Approval())
    assert not c.pending(c.WITH_APPROVAL, items, NOW_MS, approved) or approved.kwh == c.APPROVED_KWH_MAX
    assert approved.until_ms >= max((b.end_ms for b in c.offered(items, NOW_MS)), default=0)


@given(blocks)
def test_offered_blocks_are_runnable_and_within_a_day(items):
    for b in c.offered(items, NOW_MS):
        assert b in items and b.end_ms > NOW_MS and b.start_ms < NOW_MS + c.DAY_MS
        assert b.end_ms - b.start_ms >= c.MIN_MS[b.code]


# ---- booking and load profile ----


@given(
    st.floats(-5, 20),
    st.floats(-5, 20),
    st.one_of(st.none(), st.floats(0, 5)),
    st.one_of(st.none(), st.floats(0, 20)),
    st.sampled_from(["h", "l"]),
)
def test_booking_never_negative_and_at_most_the_plan(d_charged, d_imported, saves, planned, code):
    block = PlanBlock(0, 1, code, kwh=planned or 0.0, saves_eur=saves, grid_kwh=planned)
    record = c.start_record(code, "x", c.Counters(100, 50, 500), block)
    kwh, eur = c.book(record, c.Counters(100 + d_charged, 50, 500 + d_imported))
    assert kwh >= 0 and eur >= 0
    if saves is not None:
        assert eur <= round(saves, 3) + 1e-9  # booked to 0.001 €


@given(
    st.lists(st.floats(0, 5000), min_size=24, max_size=24),
    st.floats(0, 1e6),
    st.floats(-10, 100),
    st.integers(1000, 6000),
)
def test_learning_changes_at_most_one_hour_and_stays_plausible(profile, meter, delta_kwh, seconds):
    learned = c.learn_profile(profile, meter, NOW, meter + delta_kwh, NOW + timedelta(seconds=seconds))
    changed = [h for h in range(24) if learned[h] != profile[h]]
    assert len(changed) <= 1
    for h in changed:
        assert h == NOW.hour and 0 <= learned[h] < c.MAX_W


# ---- switching ----

OWN = [c.NORMAL, c.HOLD, c.HOLD_NO_PV, c.CHARGE]
"""Every setting the EMS itself makes."""

situations = st.builds(
    sw.Situation,
    mode=st.sampled_from(c.MODES),
    target=st.sampled_from([c.NORMAL, c.HOLD, c.CHARGE]),
    current=st.sampled_from([*OWN, c.UNREACHABLE, "Anders (VPP / x / 0 W)"]),
    ready=st.booleans(),
    set_by_ems=st.sampled_from(OWN),
    trigger=st.sampled_from(["soll", "modus", "pruefen", "pv"]),
    now=st.just(NOW),
    switch_failed=st.booleans(),
    foreign_reported=st.one_of(
        st.none(), st.builds(sw.ForeignReport, st.just("Anders (VPP / x / 0 W)"), st.just(NOW))
    ),
    inverter_changed_at=st.one_of(st.none(), st.datetimes(timezones=st.just(TZ)).map(lambda t: min(t, NOW))),
    hold_no_pv=st.booleans(),
)


def wanted(s: sw.Situation) -> str:
    """What the inverter should end up at: the target, a hold refined by PV, normal when not active."""
    goal = s.target if s.mode in (c.WITH_APPROVAL, c.AUTOMATIC) else c.NORMAL
    return c.HOLD_NO_PV if goal == c.HOLD and s.hold_no_pv else goal


@given(situations)
def test_switching_decisions(s):
    d = sw.decide(s)
    foreign = s.current not in (s.set_by_ems, c.NORMAL)
    if not s.ready:
        assert d.kind is sw.Kind.NOTHING
    if d.kind is sw.Kind.SWITCH:
        assert d.goal != s.current
        assert d.goal == wanted(s)  # never a hold variant the PV does not call for
        assert not foreign or s.switch_failed  # a foreign setting is never overridden
    if d.kind is not sw.Kind.NOTHING:
        assert d.goal in OWN
    if s.mode == c.PLAN_ONLY:
        assert d.kind is sw.Kind.NOTHING or d.goal == c.NORMAL
    if d.kind is sw.Kind.REPORT_FOREIGN:
        assert foreign and not s.switch_failed


@given(situations)
def test_own_hold_follows_the_pv(s):
    """While the EMS holds, a change of the PV situation always switches to the other own hold."""
    if s.mode == c.PLAN_ONLY or s.target != c.HOLD or not s.ready or s.set_by_ems not in c.HOLDS:
        return
    s = sw.Situation(**{**s.__dict__, "current": s.set_by_ems})
    d = sw.decide(s)
    if s.current == wanted(s):
        assert d.kind is sw.Kind.NOTHING
    else:
        assert d.kind is sw.Kind.SWITCH and d.goal == wanted(s)


# A minimal Sungrow: three entities that take the commands one by one.
inverter_states = st.fixed_dictionaries(
    {
        "ems_modus": st.sampled_from([c.SELF_CONSUMPTION, c.FORCED_MODE, "VPP", "External EMS mode"]),
        "erzwungen": st.sampled_from([c.FORCED_STOP, c.FORCED_CHARGE, "Forced discharge"]),
        "entladen_max": st.sampled_from([0, 10, 100, 101, 5900]),
    }
)
OWN_SETTINGS = {
    c.NORMAL: {"ems_modus": c.SELF_CONSUMPTION, "erzwungen": c.FORCED_STOP, "entladen_max": 5900},
    c.HOLD: {"ems_modus": c.SELF_CONSUMPTION, "erzwungen": c.FORCED_STOP, "entladen_max": 10},
    c.HOLD_NO_PV: {"ems_modus": c.FORCED_MODE, "erzwungen": c.FORCED_STOP, "entladen_max": 5900},
    c.CHARGE: {"ems_modus": c.FORCED_MODE, "erzwungen": c.FORCED_CHARGE, "entladen_max": 5900},
}


def run(start: dict, steps: list[sw.Step]) -> list[str]:
    """The inverter state after each command (index 0: before the first)."""
    state, seen = (
        dict(start),
        [c.inverter_state(start["ems_modus"], start["erzwungen"], start["entladen_max"])],
    )
    for x in steps:
        if x.key in state:
            state[x.key] = x.value
        seen.append(c.inverter_state(state["ems_modus"], state["erzwungen"], state["entladen_max"]))
    return seen


goals = st.sampled_from(OWN)


@given(inverter_states, goals, st.floats(0, 50), st.booleans(), st.integers(c.HOLD_MAX_W + 1, 20000))
def test_commands_reach_the_goal_from_any_setting(start, goal, grid_kw, power, upper):
    """Whatever is set at the inverter (also something foreign or half switched), the commands end at the
    goal; the executor confirms exactly this."""
    steps = sw.commands_for(goal, grid_kw=grid_kw, power_configured=power, normal_discharge_w=upper)
    assert run(start, steps)[-1] == goal
    kinds = [(x.kind, x.key) for x in steps]
    assert len(kinds) == len(set(kinds))
    for x in steps:
        if x.key == "ladeleistung_erzwungen":
            assert 0 <= x.value <= GRID_KW_MAX * 1000


@given(goals, goals, st.integers(c.HOLD_MAX_W + 1, 20000))
def test_switching_between_own_settings_never_frees_the_battery_on_the_way(start, goal, upper):
    """If a switch stops after any command (Modbus timeout, restart), a battery that was held or charging
    is never left free to discharge unless normal operation was the goal; from one hold to the other it
    stays held throughout."""
    steps = sw.commands_for(goal, grid_kw=5, power_configured=True, normal_discharge_w=upper)
    seen = run(OWN_SETTINGS[start] | {"entladen_max": upper if start != c.HOLD else 10}, steps)
    if start != c.NORMAL and goal != c.NORMAL:
        assert c.NORMAL not in seen
    if start in c.HOLDS and goal in c.HOLDS:
        assert all(x in c.HOLDS for x in seen)


START_AT = datetime(2026, 11, 17, 6, 0, tzinfo=TZ)
pv_readings = st.one_of(
    st.none(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.floats(-10, 400),
    st.sampled_from([49.9, 50, 150]),
)


@given(pv_readings, st.booleans(), st.one_of(st.none(), st.integers(0, 3 * sw.HOLD_VARIANT_MIN_S)))
def test_hold_variant_rules(pv_w, no_pv, ago_s):
    since = None if ago_s is None else START_AT - timedelta(seconds=ago_s)
    last = sw.HoldVariant(no_pv, since)
    new = sw.hold_variant(pv_w, last, START_AT)
    may_change = since is None or ago_s >= sw.HOLD_VARIANT_MIN_S
    if not may_change:
        assert new == last
        return
    known = pv_w is not None and math.isfinite(pv_w)
    if not known or pv_w >= sw.PV_FROM_W:
        assert new.no_pv is False  # unknown or enough PV: the hold that lets PV charge
    elif pv_w < sw.NO_PV_BELOW_W:
        assert new.no_pv is True
    else:
        assert new.no_pv is no_pv
    assert (new.since == START_AT) == (new.no_pv != no_pv or since is None)


@given(st.lists(pv_readings, min_size=1, max_size=300))
def test_hold_variant_over_a_day_never_toggles_faster_than_allowed(minutes):
    """PV read once a minute: the variant changes at most once per HOLD_VARIANT_MIN_S, and a PV signal that
    stays inside the band changes nothing after the start."""
    v = sw.HoldVariant()
    changes = []
    for i, pv_w in enumerate(minutes):
        now = START_AT + timedelta(minutes=i)
        new = sw.hold_variant(pv_w, v, now)
        if new.since != v.since and v.since is not None:
            changes.append(now)
        v = new
    for a, b in itertools.pairwise(changes):
        assert (b - a).total_seconds() >= sw.HOLD_VARIANT_MIN_S
    assert len(changes) <= len(minutes) * 60 // sw.HOLD_VARIANT_MIN_S + 1
    inside = all(x is not None and sw.NO_PV_BELOW_W <= x < sw.PV_FROM_W for x in minutes[1:])
    if inside:
        assert changes == []


# ---- prices ----


@given(st.lists(st.tuples(st.integers(-4, 200), st.floats(-50, 200, allow_nan=False)), max_size=20))
def test_price_of_the_quarter_hour(raw):
    prices = [[NOW_MS + i * QUARTER, p] for i, p in raw]
    price = price_at(prices, NOW + timedelta(minutes=7))
    assert price is None or price in [p for t, p in prices if t == NOW_MS]


json_like = st.recursive(
    st.none() | st.text(max_size=8) | st.floats() | st.integers() | st.booleans(),
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.text(max_size=8), inner, max_size=4),
    max_leaves=12,
)


@given(st.dictionaries(st.sampled_from(["prices", "x"]), json_like, max_size=2))
def test_any_tibber_response_gives_a_sorted_price_list(response):
    prices = prices_from_tibber(response)
    assert prices == sorted(prices)
    assert all(isinstance(t, int) and isinstance(p, float) for t, p in prices)
