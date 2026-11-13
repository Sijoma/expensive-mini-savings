"""Executor rules (control.py): the former YAML templates, case by case."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.ems import control as c
from custom_components.ems.adapter import PlanBlock

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=TZ)
MIN = 60_000


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def block(
    was: str, start_min: int, length_min: int, *, kwh: float = 0.0, spart: float | None = 0.5, netz=None
):
    von = ms(NOW) + start_min * MIN
    return PlanBlock(
        start_ms=von,
        end_ms=von + length_min * MIN,
        code=was,
        kwh=kwh,
        saves_eur=spart,
        ct=40.0,
        grid_kwh=netz,
    )


APPROVED = c.Approval(until_ms=ms(NOW) + 24 * 60 * MIN, codes=frozenset({"h", "l"}), kwh=10.0)


def tgt(blocks, *, mode=c.AUTOMATIC, fresh=True, approval=APPROVED, active=c.NORMAL, soc=50.0, at=0):
    return c.target(mode, blocks, ms(NOW) + at * MIN, fresh, approval, active, soc)


LEARN_AT = datetime(2026, 11, 10, 19, 0, 10, tzinfo=TZ)
HOUR = timedelta(hours=1)


# ---- target ----


def test_only_planning_never_switches():
    assert tgt([block("h", -10, 60)], mode=c.PLAN_ONLY) == c.NORMAL


def test_automatic_runs_the_current_block():
    assert tgt([block("h", -10, 60)]) == c.HOLD
    assert tgt([block("l", -10, 60, kwh=3)]) == c.CHARGE
    assert tgt([block("h", 10, 60)]) == c.NORMAL  # not yet


def test_stale_plan_means_normal():
    assert tgt([block("h", -10, 60)], fresh=False) == c.NORMAL


def test_minimum_length_unless_already_running():
    short_hold = [block("h", -5, 20)]
    assert tgt(short_hold) == c.NORMAL
    assert tgt(short_hold, active=c.HOLD) == c.HOLD
    assert tgt([block("l", -5, 15, kwh=1)]) == c.CHARGE  # 15 min is enough for grid charging


def test_small_savings_are_not_run_unless_running():
    small = [block("h", -10, 60, spart=0.04)]
    assert tgt(small) == c.NORMAL
    assert tgt(small, active=c.HOLD) == c.HOLD
    assert tgt([block("h", -10, 60, spart=None)]) == c.HOLD


def test_charging_holds_at_98_percent_and_stops_without_soc():
    charge = [block("l", -10, 60, kwh=3)]
    assert tgt(charge, soc=97.9) == c.CHARGE
    assert tgt(charge, soc=98) == c.HOLD  # full: keep the charge
    assert tgt(charge, soc=98, mode=c.WITH_APPROVAL, approval=c.Approval(until_ms=APPROVED.until_ms,
               codes=frozenset({"l"}), kwh=10)) == c.HOLD  # fmt: skip
    assert tgt(charge, soc=None) == c.NORMAL


def test_with_approval_needs_the_code_and_time():
    hold = [block("h", -10, 60)]
    assert tgt(hold, mode=c.WITH_APPROVAL) == c.HOLD
    only_charge = c.Approval(until_ms=APPROVED.until_ms, codes=frozenset({"l"}), kwh=10)
    assert tgt(hold, mode=c.WITH_APPROVAL, approval=only_charge) == c.NORMAL
    expired = c.Approval(until_ms=ms(NOW) - MIN, codes=frozenset({"h"}))
    assert tgt(hold, mode=c.WITH_APPROVAL, approval=expired) == c.NORMAL


def test_with_approval_charging_only_up_to_the_approved_amount():
    charge = [block("l", -10, 120, kwh=10.0)]
    ok = c.Approval(until_ms=APPROVED.until_ms, codes=frozenset({"l"}), kwh=7.6)  # 7.6 × 1.25 + 0.5 = 10
    assert tgt(charge, mode=c.WITH_APPROVAL, approval=ok) == c.CHARGE
    too_little = c.Approval(until_ms=APPROVED.until_ms, codes=frozenset({"l"}), kwh=7.5)
    assert tgt(charge, mode=c.WITH_APPROVAL, approval=too_little) == c.NORMAL


# ---- approvals ----


def test_offered_are_the_runnable_blocks_of_the_next_24h():
    blocks = [block("h", 10, 60), block("h", 10, 20), block("l", 30, 60, spart=0.01), block("h", 25 * 60, 60)]
    assert c.offered(blocks, ms(NOW)) == [blocks[0]]


def test_pending_until_approved_or_rejected():
    blocks = [block("l", 6 * 60 + 30, 135, kwh=10.5), block("h", 8 * 60 + 45, 150, netz=1.6)]
    none = c.Approval()
    assert c.pending(c.WITH_APPROVAL, blocks, ms(NOW), none)
    assert not c.pending(c.AUTOMATIC, blocks, ms(NOW), none)
    approved = c.approve(blocks, NOW, none)
    assert approved.codes == {"h", "l"} and approved.kwh == 10.5
    assert approved.until_ms == ms(datetime(2026, 11, 11, 14, 0, tzinfo=TZ))  # next 14:00
    assert not c.pending(c.WITH_APPROVAL, blocks, ms(NOW), approved)
    rejected = c.reject(blocks, NOW, none)
    assert rejected.rejected_until_ms == blocks[1].end_ms
    assert not c.pending(c.WITH_APPROVAL, blocks, ms(NOW), rejected)


def test_only_charge_approved_leaves_the_hold_pending():
    """The split of a grid charge into charge + hold needs a new approval for the hold."""
    blocks = [block("l", 60, 135, kwh=10.5), block("h", 195, 150, netz=1.6)]
    only_charge = c.Approval(until_ms=APPROVED.until_ms, codes=frozenset({"l"}), kwh=10.5)
    assert c.pending(c.WITH_APPROVAL, blocks, ms(NOW), only_charge)


def test_approval_lasts_until_the_last_block_if_later_than_14():
    blocks = [block("h", 20 * 60, 60)]  # tomorrow 16:00–17:00
    approved = c.approve(blocks, NOW, c.Approval())
    assert approved.until_ms == blocks[0].end_ms


def test_withdraw_ends_the_approval_now():
    assert c.withdraw(NOW, APPROVED).until_ms == ms(NOW) - 1
    assert tgt([block("h", -10, 60)], mode=c.WITH_APPROVAL, approval=c.withdraw(NOW, APPROVED)) == c.NORMAL
    assert (
        tgt([block("h", 10, 60)], mode=c.WITH_APPROVAL, approval=c.withdraw(NOW, APPROVED), at=20) == c.NORMAL
    )


# ---- inverter ----


def test_inverter_state():
    assert c.inverter_state(c.SELF_CONSUMPTION, c.FORCED_STOP, 5900) == c.NORMAL
    assert c.inverter_state(c.SELF_CONSUMPTION, c.FORCED_STOP, 10) == c.HOLD
    assert c.inverter_state(c.FORCED_MODE, c.FORCED_CHARGE, 5900) == c.CHARGE
    assert c.inverter_state("VPP", c.FORCED_STOP, 5900).startswith("Anders (VPP")
    assert c.inverter_state(None, None, None) == c.UNREACHABLE
    assert c.inverter_state("unavailable", "unavailable", None) == c.UNREACHABLE


# ---- booking ----


def test_booking_scales_the_plan_by_the_measured_quantity():
    start = c.Counters(charged=100.0, charged_pv=60.0, imported=500.0)
    rec = c.start_record("l", "2026-11-11T02:30+01:00", start, PlanBlock(0, 1, "l", kwh=10.5, saves_eur=1.8))
    half = c.Counters(charged=100.0 + 5.045, charged_pv=60.0, imported=505.3)  # 5.045 DC = 5.25 AC
    kwh, eur = c.book(rec, half)
    assert kwh == 5.045 and eur == 0.9
    full = c.Counters(charged=120.0, charged_pv=60.0, imported=520.0)
    assert c.book(rec, full)[1] == 1.8  # never more than planned


def test_booking_a_hold_uses_grid_import():
    start = c.Counters(charged=0, charged_pv=0, imported=10.0)
    rec = c.start_record(
        "h", "2026-11-10T00:00+01:00", start, PlanBlock(0, 1, "h", saves_eur=0.55, grid_kwh=2.3)
    )
    assert c.book(rec, c.Counters(0, 0, 13.2)) == (3.2, 0.55)
    assert c.book(rec, c.Counters(0, 0, 11.15)) == (1.15, 0.275)


def test_booking_without_plan_values():
    rec = c.start_record("h", "x", c.Counters(0, 0, 0), None)
    assert c.book(rec, c.Counters(0, 0, 1)) == (1.0, 0.0)
    rec = c.start_record("h", "x", c.Counters(0, 0, 0), PlanBlock(0, 1, "h", saves_eur=0.3))
    assert c.book(rec, c.Counters(0, 0, 1)) == (1.0, 0.3)
    assert c.book(rec, c.Counters(0, 0, 0)) == (0.0, 0.0)


# ---- load profile ----


def test_load_profile_learns_15_percent_of_the_past_hour():
    before = datetime(2026, 11, 10, 19, 0, 10, tzinfo=TZ)
    after = before + timedelta(hours=1)
    p = c.learn_profile(c.START_PROFILE, 1000.0, before, 1001.2, after)  # 1.2 kWh in 1 h = 1200 W
    assert p[19] == round(1000 * 0.85 + 1200 * 0.15)
    assert p[:19] == c.START_PROFILE[:19] and p[20:] == c.START_PROFILE[20:]


def test_load_profile_ignores_odd_readings():
    before = datetime(2026, 11, 10, 19, 0, 10, tzinfo=TZ)
    assert (
        c.learn_profile(c.START_PROFILE, 1000.0, before, 1001.0, before + timedelta(minutes=20))
        == c.START_PROFILE
    )
    assert (
        c.learn_profile(c.START_PROFILE, 1000.0, before, 1020.0, before + timedelta(hours=1))
        == c.START_PROFILE
    )
    assert (
        c.learn_profile(c.START_PROFILE, None, before, 1001.0, before + timedelta(hours=1)) == c.START_PROFILE
    )
    assert c.learn_profile([], 1000.0, before, 1000.0, before + timedelta(hours=1))[19] == round(1000 * 0.85)


def test_rejecting_a_running_grid_charge_leaves_nothing_pending():
    """Found by Hypothesis: the over-approved check ran against a rejected (expired) approval."""
    running = block("l", -15, 30, kwh=1.0, spart=None)
    rejected = c.reject([running], NOW, c.Approval())
    assert not c.pending(c.WITH_APPROVAL, [running], ms(NOW), rejected)


def test_over_approved_grid_charge_is_pending_while_the_approval_holds():
    approved = c.approve([block("l", 0, 60, kwh=2.0)], NOW, c.Approval())
    grown = [block("l", 0, 60, kwh=6.0)]
    assert c.pending(c.WITH_APPROVAL, grown, ms(NOW), approved)


def test_inverter_text_survives_infinite_values():
    """Found by Hypothesis: int(inf) failed while building the text."""
    assert c.inverter_state("x", "y", float("inf")) == "Anders (x / y / inf W)"
    assert c.inverter_state("x", "y", 5900.0) == "Anders (x / y / 5900 W)"


def test_a_grid_charge_not_worth_it_does_not_keep_an_approval_open():
    """Found by Hypothesis: a block never offered (saves < 0.05 €) still counted as unapproved kWh."""
    items = [block("h", 0, 30), block("l", 0, 15, kwh=3.0, spart=0.0)]
    approved = c.approve(items, NOW, c.Approval())
    assert not c.pending(c.WITH_APPROVAL, items, ms(NOW), approved)


def test_a_grid_charge_beyond_the_offer_window_does_not_keep_an_approval_open():
    """Found by Hypothesis: approval until 20:15 tomorrow (hold block), a grid charge at 20:00 tomorrow is not
    offered yet (24 h window) but counted as unapproved kWh."""
    items = [block("h", 20 * 60 + 15, 240), block("l", 24 * 60, 15, kwh=1.0, spart=None)]
    approved = c.approve(items, NOW, c.Approval())
    assert not c.pending(c.WITH_APPROVAL, items, ms(NOW), approved)


# ---- boundaries and values found missing by mutation testing (mutmut) ----


def test_approval_record_round_trip_and_bad_input():
    a = c.Approval(until_ms=5, codes=frozenset({"l", "h"}), kwh=3.5, rejected_until_ms=7)
    assert a.as_dict() == {"until_ms": 5, "codes": ["h", "l"], "kwh": 3.5, "rejected_until_ms": 7}
    assert c.Approval.from_dict(a.as_dict()) == a
    assert c.Approval.from_dict(None) == c.Approval() == c.Approval.from_dict({"codes": None, "kwh": None})


def test_approval_ends_today_at_14_if_before_and_tomorrow_otherwise():
    morning = NOW.replace(hour=8, minute=30, second=45, microsecond=5)
    assert c.next_approval_end(morning) == NOW.replace(hour=14, minute=0, second=0, microsecond=0)
    at_14 = NOW.replace(hour=14, minute=0, second=0, microsecond=0)
    assert c.next_approval_end(at_14) == at_14 + timedelta(days=1)


def test_approving_lifts_an_earlier_rejection():
    items = [block("h", 0, 60)]
    rejected = c.reject(items, NOW, c.Approval())
    assert rejected.until_ms == ms(NOW) - 1 and rejected.rejected_until_ms == ms(NOW) + 60 * MIN
    approved = c.approve(items, NOW, rejected)
    assert approved.rejected_until_ms == ms(NOW)
    assert approved.kwh == 0.0  # a hold approves no grid charge


def test_block_runs_from_its_first_to_its_last_millisecond():
    b = block("h", 0, 60)
    others = [block("l", -120, 30), b]  # a block that does not match is skipped, not the end of the search
    for now_ms, expected in [(b.start_ms, c.HOLD), (b.end_ms - 1, c.HOLD), (b.end_ms, c.NORMAL)]:
        assert c.target(c.AUTOMATIC, others, now_ms, True, APPROVED, c.NORMAL, 50.0) == expected
    assert c.offered([b], b.end_ms) == [] and c.offered([b], b.end_ms - 1) == [b]


def test_minimum_saving_and_length_are_inclusive():
    assert c.offered([block("h", 0, 30, spart=c.MIN_SAVING_EUR)], ms(NOW))
    assert c.offered([block("l", 0, 15, kwh=1.0)], ms(NOW))


def test_approval_holds_until_its_last_millisecond():
    a = c.Approval(until_ms=ms(NOW), codes=frozenset({"h"}))
    assert c.target(c.WITH_APPROVAL, [block("h", -30, 60)], ms(NOW), True, a, c.NORMAL, 50.0) == c.HOLD
    grown = c.Approval(until_ms=ms(NOW), codes=frozenset({"l"}), kwh=0.0)
    assert c.pending(c.WITH_APPROVAL, [block("l", -15, 30, kwh=3.0)], ms(NOW), grown)  # over the amount


def test_a_block_starting_when_the_rejection_ends_is_pending_again():
    a = c.Approval(rejected_until_ms=ms(NOW) + 60 * MIN)
    assert c.pending(c.WITH_APPROVAL, [block("h", 60, 60)], ms(NOW), a)
    assert not c.pending(c.WITH_APPROVAL, [block("h", 59, 60)], ms(NOW), a)


def test_charge_counted_up_to_but_not_at_the_approval_end():
    items = [block("l", 30, 15, kwh=2.0)]
    assert c.charge_kwh_until(items, ms(NOW), ms(NOW) + 30 * MIN) == 0.0
    assert c.charge_kwh_until(items, ms(NOW), ms(NOW) + 30 * MIN + 1) == 2.0


def test_request_window_and_timing():
    since = NOW.replace(hour=12) - timedelta(minutes=10)
    assert c.should_request(NOW.replace(hour=22, minute=29), since, None)
    assert not c.should_request(NOW.replace(hour=22, minute=30), since, None)
    assert not c.should_request(NOW.replace(hour=22, minute=45), since, None)
    at = NOW.replace(hour=12, minute=0)
    assert c.should_request(at, at - timedelta(seconds=c.REQUEST_AFTER_S), None)
    assert not c.should_request(at, at - timedelta(seconds=c.REQUEST_AFTER_S - 1), None)
    assert not c.should_request(at, since, since)  # asked exactly when it became pending


def test_start_record_and_booking_when_nothing_happened():
    counters = c.Counters(100.0, 40.0, 500.0)
    record = c.start_record("l", "2026-11-10T02:00+01:00", counters, block("l", 0, 60, kwh=4.0, spart=1.2))
    assert record == {
        "action": "l",
        "start": "2026-11-10T02:00+01:00",
        "grid_charged_kwh": 60.0,
        "imported_kwh": 500.0,
        "saves_eur": 1.2,
        "planned_kwh": 4.0,
    }
    assert c.book(record, counters) == (0.0, 0.0)  # nothing charged: nothing saved
    assert c.book(record, c.Counters(102.0, 40.0, 500.0)) == (
        2.0,
        round(1.2 * (2.0 / c.GRID_TO_BATTERY) / 4.0, 3),
    )


def test_load_profile_learns_the_exact_value_within_the_window():
    profile = [0.0] * 24
    one_kwh = (1000.0, NOW.replace(hour=7), 1001.0)  # 1 kWh in the hour from 07:00 = 1000 W
    learned = c.learn_profile(profile, one_kwh[0], one_kwh[1], one_kwh[2], one_kwh[1] + timedelta(hours=1))
    assert learned[7] == 150 and sum(learned) == 150
    for seconds, learns in [(3000, False), (3001, True), (4199, True), (4200, False)]:
        out = c.learn_profile(profile, 0.0, NOW, 0.5, NOW + timedelta(seconds=seconds))
        assert (out != profile) is learns, seconds
    assert (
        c.learn_profile(profile, 0.0, NOW, c.MAX_W / 1000, NOW + timedelta(hours=1)) == profile
    )  # implausible


def test_inverter_text_without_discharge_value():
    assert c.inverter_state("VPP", c.FORCED_STOP, None) == "Anders (VPP / Stop (default) / -1 W)"


def test_load_profile_value_and_missing_readings():
    profile = [0.0] * 24
    hour = NOW.replace(hour=7)
    learned = c.learn_profile(profile, 1000.0, hour, 1010.0, hour + timedelta(hours=1))  # 10 kWh = 10 kW
    assert learned[7] == 1500
    assert c.learn_profile(profile, 1000.0, hour, None, hour + timedelta(hours=1)) == profile
    assert c.learn_profile(profile, None, hour, 1010.0, hour + timedelta(hours=1)) == profile


def test_booking_a_small_planned_quantity():
    record = c.start_record("h", "x", c.Counters(0, 0, 100.0), block("h", 0, 60, spart=0.4, netz=0.5))
    assert c.book(record, c.Counters(0, 0, 100.25)) == (
        0.25,
        0.2,
    )  # half the planned 0.5 kWh: half the saving


def test_a_grid_charge_without_quantity_approves_no_kwh():
    approved = c.approve([block("l", 0, 30, kwh=0.0)], NOW, c.Approval())
    assert approved.codes == {"l"} and approved.kwh == 0.0


def test_the_offer_window_ends_24_hours_from_now():
    assert c.offered([block("h", 24 * 60 - 1, 60)], ms(NOW))
    assert not c.offered([block("h", 24 * 60, 60)], ms(NOW))
