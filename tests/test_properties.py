"""Rules that must hold for any input (Hypothesis), for the pure modules of the integration:
inverter classification, approvals and target, booking, load profile, switching decisions, prices.

`uv run pytest --hypothesis-profile=thorough` runs 2000 examples per rule (before a release)."""

from __future__ import annotations

import itertools
import math
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import event, given, settings
from hypothesis import strategies as st

from custom_components.ems import control as c
from custom_components.ems import heat_pump_control as hp
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


# ---- heat pump PV boost ----
# The rules of the former automation, written out here (not taken from the module, so a changed constant
# fails): on after 10 min of export > 1200 W or charging > 2500 W, 09:00–16:30, battery > 80 %, 30 min
# pause; off after 5 min of import or discharge > 300 W, at 17:00 at the latest.
ON_FROM, ON_UNTIL, OFF_AT = time(9), time(16, 30), time(17)

DAY_START = datetime(2026, 11, 17, 0, 0, tzinfo=TZ)
# (grid W, battery W) of a phase; a little noise around the thresholds
PHASES = {
    "export": (st.floats(-3000, -1150), st.floats(-500, 200)),
    "charge": (st.floats(-1300, 100), st.floats(-3500, -2450)),
    "import": (st.floats(250, 2000), st.floats(-100, 100)),
    "discharge": (st.floats(-200, 200), st.floats(250, 2000)),
    "quiet": (st.floats(-400, 400), st.floats(-400, 400)),
    "missing": (st.none(), st.none()),
}


@st.composite
def phase(draw):
    kind = draw(st.sampled_from(["export", "export", "export", "charge", "charge", *sorted(PHASES)]))
    grid, battery = PHASES[kind]
    soc = draw(st.sampled_from([95, 95, 95, 80.1, 80, 79.9, 50, None]) | st.floats(0, 100))
    program = draw(st.booleans() | st.just(True))
    return [(draw(grid), draw(battery), soc, program) for _ in range(draw(st.integers(1, 40)))]


minutes_of_day = st.lists(phase(), min_size=1, max_size=12).map(lambda ps: [m for p in ps for m in p])
start_minutes = st.one_of(
    st.sampled_from(
        [8 * 60 + 40, 9 * 60, 11 * 60, 12 * 60, 14 * 60, 16 * 60 + 10, 16 * 60 + 25, 16 * 60 + 40]
    ),
    st.integers(0, 24 * 60 - 1),
)


@settings(max_examples=400)
@given(start_minutes, minutes_of_day, st.booleans())
def test_heat_pump_boost_follows_the_rules_over_a_day(start_min, minutes, foreign_start):
    """Closed loop, one reading per minute, every switch confirmed at once. Checks the automation's rules from
    the trace itself: a boost starts only after 10 min of surplus, between 9:00 and 16:30, above 80 %, in
    the normal program and 30 min after the last one; it ends after 5 min of import or discharge and at
    17:00; a boost the EMS did not set is never touched."""
    timers, boosted, changed_at, own = hp.Timers(), foreign_start, None, False
    history: list[tuple[float | None, float | None]] = []
    for i, (grid, battery, soc, program) in enumerate(minutes):
        now = DAY_START + timedelta(minutes=start_min + i)
        r = hp.Readings(
            now=now, grid_w=grid, battery_w=battery, soc=soc, boosted=boosted, blocked=False,
            boosted_changed_at=changed_at, operating_mode="program" if program else "comfort",
            operating_mode_required="program",
        )  # fmt: skip
        history.append((grid, battery))
        timers = hp.advance(timers, r)
        d = hp.decide(hp.Situation(hp.AUTOMATIC, r, timers, own, "pruefen"))
        if foreign_start and boosted and not own:
            assert d.kind in (hp.Kind.NOTHING, hp.Kind.REPORT_FOREIGN)  # never touched
            continue
        t = now.time()
        if own:
            last6 = history[-6:]
            must_end = (t >= OFF_AT or t < ON_FROM) or (
                len(last6) == 6
                and (
                    all(g is not None and g > 300 for g, _ in last6)
                    or all(b is not None and b > 300 for _, b in last6)
                )
            )
            assert (d.kind is hp.Kind.SWITCH and not d.on) == must_end  # ends exactly when it must
        if d.kind is not hp.Kind.SWITCH:
            continue
        if d.on:
            assert ON_FROM <= t < ON_UNTIL
            assert soc is not None and soc > 80 and program
            last11 = history[-11:]  # high at 11 readings = 10 min
            assert len(last11) == 11
            assert all(g is not None and -g > 1200 for g, _ in last11) or all(
                b is not None and -b > 2500 for _, b in last11
            )
            assert changed_at is None or (now - changed_at).total_seconds() >= 30 * 60
        else:
            last6 = history[-6:]
            late = t >= OFF_AT or t < ON_FROM
            assert late or (
                len(last6) == 6
                and (
                    all(g is not None and g > 300 for g, _ in last6)
                    or all(b is not None and b > 300 for _, b in last6)
                )
            )
        assert d.on != boosted
        event("boost on" if d.on else "boost off")
        boosted, own, changed_at = d.on, d.on, now


@given(start_minutes, minutes_of_day)
def test_heat_pump_plan_only_never_switches(start_min, minutes):
    timers = hp.Timers()
    for i, (grid, battery, soc, _) in enumerate(minutes):
        r = hp.Readings(DAY_START + timedelta(minutes=start_min + i), grid, battery, soc, False, False)
        timers = hp.advance(timers, r)
        assert hp.decide(hp.Situation(hp.PLAN_ONLY, r, timers, False, "pruefen")).kind is hp.Kind.NOTHING


switches = st.fixed_dictionaries(
    {"wp_sg_ready": st.booleans(), "wp_verstaerkt": st.booleans(), "wp_sperre": st.booleans()}
)


@given(switches, st.booleans(), st.booleans(), st.booleans())
def test_heat_pump_commands_reach_the_goal_and_never_block(start, on, sg_ready, block):
    """From any switch state the commands end at the goal (boost on/off, block off when configured), and no
    command ever switches the block on or leaves SG Ready off for a boost."""
    state = dict(start)
    for x in hp.commands(on, sg_ready=sg_ready, block=block):
        assert not (x.key == "wp_sperre" and x.value)
        state[x.key] = x.value
    assert state["wp_verstaerkt"] is on
    if block:
        assert state["wp_sperre"] is False
    if on and sg_ready:
        assert state["wp_sg_ready"] is True


def run_boost(start: datetime, minutes: list[tuple[float | None, float | None]], soc: float = 95):
    """Closed loop in "Automatisch"; returns the minutes at which the boost went on and off."""
    timers, boosted, changed_at, own, ons, offs = hp.Timers(), False, None, False, [], []
    for i, (grid, battery) in enumerate(minutes):
        now = start + timedelta(minutes=i)
        r = hp.Readings(now, grid, battery, soc, boosted, False, changed_at, "program", "program")
        timers = hp.advance(timers, r)
        d = hp.decide(hp.Situation(hp.AUTOMATIC, r, timers, own, "pruefen"))
        if d.kind is hp.Kind.SWITCH:
            (ons if d.on else offs).append(now)
            boosted, own, changed_at = d.on, d.on, now
    return ons, offs


STRICT = {
    "export": (st.floats(-3000, -1201), st.floats(-500, 200)),
    "import": (st.floats(301, 2000), st.floats(-100, 100)),
}
"""Phases clearly beyond the thresholds (no noise across them)."""


def flat(kind: str, n: int, draw) -> list[tuple[float | None, float | None]]:
    grid, battery = STRICT[kind]
    return [(draw(grid), draw(battery)) for _ in range(n)]


@given(st.data(), st.integers(11, 40), st.integers(6, 20), st.integers(1, 60))
def test_heat_pump_waits_30_min_after_a_boost(data, n_export, n_import, n_again):
    """Export, then import (the boost ends), then export again: the next boost never comes within 30 min."""
    minutes = [*flat("export", n_export, data.draw), *flat("import", n_import, data.draw)]
    minutes += flat("export", n_again, data.draw)
    ons, offs = run_boost(DAY_START.replace(hour=10), minutes)
    assert len(ons) >= 1 and len(offs) >= 1
    for off in offs:
        assert all(not off < on < off + timedelta(minutes=30) for on in ons)  # the spec, not 30 * 60


@given(st.data(), st.integers(0, 29), st.integers(1, 240))
def test_heat_pump_boost_ends_at_17_at_the_latest(data, start_min, n):
    """A boost started in the afternoon and kept up by surplus is over by 17:00."""
    start = DAY_START.replace(hour=16) + timedelta(minutes=start_min) - timedelta(minutes=11)
    ons, offs = run_boost(start, flat("export", 11 + n, data.draw))
    assert ons and ons[0].time() < time(16, 30)
    end = start + timedelta(minutes=11 + n - 1)
    if end.time() >= time(17) or end.date() > start.date():
        assert offs and offs[0] <= DAY_START.replace(hour=17)
