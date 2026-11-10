"""Executor rules (control.py): the former YAML templates, case by case."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from custom_components.ems import control as c

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=TZ)
MIN = 60_000


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def block(
    was: str, start_min: int, length_min: int, *, kwh: float = 0.0, spart: float | None = 0.5, netz=None
):
    von = ms(NOW) + start_min * MIN
    return {"von": von, "bis": von + length_min * MIN, "was": was, "kwh": kwh, "spart": spart, "ct": 40.0,
            "netz_kwh": netz}  # fmt: skip


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
    assert rejected.rejected_until_ms == blocks[1]["bis"]
    assert not c.pending(c.WITH_APPROVAL, blocks, ms(NOW), rejected)


def test_only_charge_approved_leaves_the_hold_pending():
    """The split of a grid charge into charge + hold needs a new approval for the hold."""
    blocks = [block("l", 60, 135, kwh=10.5), block("h", 195, 150, netz=1.6)]
    only_charge = c.Approval(until_ms=APPROVED.until_ms, codes=frozenset({"l"}), kwh=10.5)
    assert c.pending(c.WITH_APPROVAL, blocks, ms(NOW), only_charge)


def test_approval_lasts_until_the_last_block_if_later_than_14():
    blocks = [block("h", 20 * 60, 60)]  # tomorrow 16:00–17:00
    approved = c.approve(blocks, NOW, c.Approval())
    assert approved.until_ms == blocks[0]["bis"]


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
    assert c.inverter_state(None, None, None) == "Anders (None / None / -1 W)"


# ---- booking ----


def test_booking_scales_the_plan_by_the_measured_quantity():
    start = c.Counters(charged=100.0, charged_pv=60.0, imported=500.0)
    rec = c.start_record("l", "2026-11-11T02:30+01:00", start, {"spart": 1.8, "kwh": 10.5})
    half = c.Counters(charged=100.0 + 5.045, charged_pv=60.0, imported=505.3)  # 5.045 DC = 5.25 AC
    kwh, eur = c.book(rec, half)
    assert kwh == 5.045 and eur == 0.9
    full = c.Counters(charged=120.0, charged_pv=60.0, imported=520.0)
    assert c.book(rec, full)[1] == 1.8  # never more than planned


def test_booking_a_hold_uses_grid_import():
    start = c.Counters(charged=0, charged_pv=0, imported=10.0)
    rec = c.start_record("h", "2026-11-10T00:00+01:00", start, {"spart": 0.55, "netz_kwh": 2.3})
    assert c.book(rec, c.Counters(0, 0, 13.2)) == (3.2, 0.55)
    assert c.book(rec, c.Counters(0, 0, 11.15)) == (1.15, 0.275)


def test_booking_without_plan_values():
    rec = c.start_record("h", "x", c.Counters(0, 0, 0), None)
    assert c.book(rec, c.Counters(0, 0, 1)) == (1.0, 0.0)
    rec = c.start_record("h", "x", c.Counters(0, 0, 0), {"spart": 0.3})
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
