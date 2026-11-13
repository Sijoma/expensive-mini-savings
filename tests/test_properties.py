"""Rules that must hold for any input (Hypothesis), for the pure modules of the integration:
inverter classification, approvals and target, booking, load profile, switching decisions, prices.

`uv run pytest --hypothesis-profile=thorough` runs 2000 examples per rule (before a release)."""

from __future__ import annotations

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

situations = st.builds(
    sw.Situation,
    mode=st.sampled_from(c.MODES),
    target=st.sampled_from([c.NORMAL, c.HOLD, c.CHARGE]),
    current=st.sampled_from([c.NORMAL, c.HOLD, c.CHARGE, c.UNREACHABLE, "Anders (VPP / x / 0 W)"]),
    ready=st.booleans(),
    set_by_ems=st.sampled_from([c.NORMAL, c.HOLD, c.CHARGE]),
    trigger=st.sampled_from(["soll", "modus", "pruefen"]),
    now=st.just(NOW),
    switch_failed=st.booleans(),
    foreign_reported=st.one_of(
        st.none(), st.builds(sw.ForeignReport, st.just("Anders (VPP / x / 0 W)"), st.just(NOW))
    ),
    inverter_changed_at=st.one_of(st.none(), st.datetimes(timezones=st.just(TZ)).map(lambda t: min(t, NOW))),
)


@given(situations)
def test_switching_decisions(s):
    d = sw.decide(s)
    foreign = s.current not in (s.set_by_ems, c.NORMAL)
    if not s.ready:
        assert d.kind is sw.Kind.NOTHING
    if d.kind is sw.Kind.SWITCH:
        assert d.goal != s.current
        assert not foreign or s.switch_failed  # a foreign setting is never overridden
    if s.mode == c.PLAN_ONLY:
        assert d.kind is sw.Kind.NOTHING or d.goal == c.NORMAL
    if d.kind is sw.Kind.REPORT_FOREIGN:
        assert foreign and not s.switch_failed


@given(st.sampled_from([c.NORMAL, c.HOLD, c.CHARGE]), st.floats(0, 50), st.booleans(), st.integers(0, 20000))
def test_commands_end_with_the_mode_and_respect_the_limit(goal, grid_kw, power, upper):
    steps = sw.commands_for(goal, grid_kw=grid_kw, power_configured=power, normal_discharge_w=upper)
    kinds = [(x.kind, x.key) for x in steps]
    assert len(kinds) == len(set(kinds))
    for x in steps:
        if x.key == "ladeleistung_erzwungen":
            assert x.value <= GRID_KW_MAX * 1000
    if goal != c.NORMAL:
        assert steps[-1].key == "ems_modus"  # the mode switches last, after limits and forced mode


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
