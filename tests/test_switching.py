"""Switching decisions without Home Assistant: switch, report a foreign setting or do nothing."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.ems import control as c
from custom_components.ems import switching as sw
from custom_components.ems.const import CONF_DISCHARGE_MAX, CONF_EMS_MODE, CONF_FORCED, CONF_FORCED_POWER

NOW = datetime(2026, 11, 13, 20, 0, tzinfo=ZoneInfo("Europe/Berlin"))
OTHER = "Anders (Forced mode / Forced discharge / 5900 W)"
LONG_AGO = NOW - timedelta(hours=1)

BASE = sw.Situation(
    mode=c.AUTOMATIC,
    target=c.HOLD,
    current=c.NORMAL,
    ready=True,
    set_by_ems=c.NORMAL,
    trigger="soll",
    now=NOW,
)


def situation(**changes) -> sw.Situation:
    return replace(BASE, **changes)


@pytest.mark.parametrize(
    ("changes", "kind", "goal"),
    [
        # the plan wants hold, the inverter runs normally: switch
        ({}, sw.Kind.SWITCH, c.HOLD),
        ({"mode": c.WITH_APPROVAL}, sw.Kind.SWITCH, c.HOLD),
        # already there, or the inverter cannot be read: nothing
        ({"current": c.HOLD, "set_by_ems": c.HOLD}, sw.Kind.NOTHING, c.NORMAL),
        ({"ready": False}, sw.Kind.NOTHING, c.NORMAL),
        # back from an own hold to normal operation
        ({"target": c.NORMAL, "current": c.HOLD, "set_by_ems": c.HOLD}, sw.Kind.SWITCH, c.NORMAL),
        # "Nur planen": the goal is normal, but only the own setting and only on a mode change
        ({"mode": c.PLAN_ONLY}, sw.Kind.NOTHING, c.NORMAL),
        ({"mode": c.PLAN_ONLY, "current": c.HOLD, "set_by_ems": c.HOLD}, sw.Kind.NOTHING, c.NORMAL),
        (
            {"mode": c.PLAN_ONLY, "current": c.HOLD, "set_by_ems": c.HOLD, "trigger": "modus"},
            sw.Kind.SWITCH,
            c.NORMAL,
        ),
        ({"mode": c.PLAN_ONLY, "current": OTHER, "trigger": "modus"}, sw.Kind.NOTHING, c.NORMAL),
        # a setting the EMS did not make: report, never override ...
        ({"current": OTHER, "inverter_changed_at": LONG_AGO}, sw.Kind.REPORT_FOREIGN, c.HOLD),
        # ... unless the own last switch failed: then it may be a half-done command, try again
        ({"current": OTHER, "switch_failed": True}, sw.Kind.SWITCH, c.HOLD),
    ],
)
def test_decide(changes, kind, goal):
    d = sw.decide(situation(**changes))
    assert d.kind is kind
    if kind is not sw.Kind.NOTHING:
        assert d.goal == goal
        assert d.current == situation(**changes).current


@pytest.mark.parametrize(
    ("changed_min_ago", "reported", "kind"),
    [
        (2, None, sw.Kind.NOTHING),  # still being changed: wait 5 min
        (6, None, sw.Kind.REPORT_FOREIGN),
        (60, sw.ForeignReport(OTHER, NOW - timedelta(hours=11)), sw.Kind.NOTHING),  # same, within 12 h
        (60, sw.ForeignReport(OTHER, NOW - timedelta(hours=13)), sw.Kind.REPORT_FOREIGN),
        (60, sw.ForeignReport("Anders (VPP / Stop (default) / 0 W)", NOW), sw.Kind.REPORT_FOREIGN),
    ],
)
def test_foreign_setting_is_reported_once_it_lasts_and_not_too_often(changed_min_ago, reported, kind):
    s = situation(
        current=OTHER,
        inverter_changed_at=NOW - timedelta(minutes=changed_min_ago),
        foreign_reported=reported,
    )
    assert sw.decide(s).kind is kind


def test_foreign_report_round_trip():
    r = sw.ForeignReport(OTHER, NOW)
    assert sw.ForeignReport.from_dict(r.as_dict()) == r
    assert sw.ForeignReport.from_dict(None) is None
    assert sw.ForeignReport.from_dict({"setting": OTHER, "at": "kaputt"}) is None


def test_commands_in_order():
    hold = sw.commands_for(c.HOLD, grid_kw=5, power_configured=True)
    assert [(x.kind, x.key, x.value) for x in hold] == [
        ("number", CONF_DISCHARGE_MAX, c.HOLD_DISCHARGE_W),
        ("select", CONF_FORCED, c.FORCED_STOP),
        ("select", CONF_EMS_MODE, c.SELF_CONSUMPTION),
    ]
    charge = sw.commands_for(c.CHARGE, grid_kw=3.5, power_configured=True)
    assert [(x.key, x.value) for x in charge] == [
        (CONF_FORCED_POWER, 3500),
        (CONF_FORCED, c.FORCED_CHARGE),
        (CONF_EMS_MODE, c.FORCED_MODE),
    ]
    normal = sw.commands_for(c.NORMAL, grid_kw=5, power_configured=True, normal_discharge_w=4000)
    assert [(x.key, x.value) for x in normal][-1] == (CONF_DISCHARGE_MAX, 4000)


def test_grid_charge_power_is_capped_and_optional():
    assert sw.commands_for(c.CHARGE, grid_kw=9, power_configured=True)[0].value == 5000
    assert [x.key for x in sw.commands_for(c.CHARGE, grid_kw=3, power_configured=False)] == [
        CONF_FORCED,
        CONF_EMS_MODE,
    ]


@pytest.mark.parametrize(
    ("at", "pending_min", "asked", "expected"),
    [
        ("12:00", 6, None, True),
        ("12:00", 3, None, False),  # wait 5 min
        ("12:00", 6, "after", False),  # already asked since it became pending
        ("12:00", 6, "before", True),  # asked about something earlier
        ("23:00", 60, None, False),  # never at night
        ("06:59", 60, None, False),
        ("07:00", 600, "after", True),  # open requests come again in the morning
        ("22:29", 6, None, True),
    ],
)
def test_should_request(at, pending_min, asked, expected):
    h, m = map(int, at.split(":"))
    now = NOW.replace(hour=h, minute=m)
    pending_since = now - timedelta(minutes=pending_min)
    asked_at = {
        None: None,
        "after": pending_since + timedelta(minutes=1),
        "before": pending_since - timedelta(1),
    }
    assert c.should_request(now, pending_since, asked_at[asked]) is expected
    assert c.should_request(now, None, None) is False


def test_commands_exactly_for_every_goal():
    def plain(steps):
        return [(x.kind, x.key, x.value) for x in steps]

    assert plain(sw.commands_for(c.CHARGE, grid_kw=3.5, power_configured=True)) == [
        ("number", CONF_FORCED_POWER, 3500),
        ("select", CONF_FORCED, c.FORCED_CHARGE),
        ("select", CONF_EMS_MODE, c.FORCED_MODE),
    ]
    assert plain(sw.commands_for(c.NORMAL, grid_kw=5, power_configured=True, normal_discharge_w=4000)) == [
        ("select", CONF_FORCED, c.FORCED_STOP),
        ("select", CONF_EMS_MODE, c.SELF_CONSUMPTION),
        ("number", CONF_DISCHARGE_MAX, 4000),
    ]
    assert plain(sw.commands_for(c.NORMAL, grid_kw=5, power_configured=False))[-1][2] == sw.NORMAL_DISCHARGE_W


def test_foreign_report_needs_setting_and_time():
    assert sw.ForeignReport.from_dict({"setting": OTHER}) is None
    assert sw.ForeignReport.from_dict({"at": NOW.isoformat()}) is None


def test_quiet_and_repeat_times_are_inclusive():
    quiet = situation(current=OTHER, inverter_changed_at=NOW - timedelta(seconds=sw.FOREIGN_QUIET_S))
    assert sw.decide(quiet).kind is sw.Kind.NOTHING
    repeat = situation(
        current=OTHER,
        inverter_changed_at=LONG_AGO,
        foreign_reported=sw.ForeignReport(OTHER, NOW - timedelta(seconds=sw.FOREIGN_REPEAT_S)),
    )
    assert sw.decide(repeat).kind is sw.Kind.NOTHING


# ---- hold with or without PV ----


@pytest.mark.parametrize(
    ("changes", "kind", "goal"),
    [
        # without PV the hold runs as Forced mode + Stop ...
        ({"hold_no_pv": True}, sw.Kind.SWITCH, c.HOLD_NO_PV),
        # ... and the EMS changes between its own two holds both ways
        ({"hold_no_pv": True, "current": c.HOLD, "set_by_ems": c.HOLD}, sw.Kind.SWITCH, c.HOLD_NO_PV),
        ({"current": c.HOLD_NO_PV, "set_by_ems": c.HOLD_NO_PV}, sw.Kind.SWITCH, c.HOLD),
        (
            {"hold_no_pv": True, "current": c.HOLD_NO_PV, "set_by_ems": c.HOLD_NO_PV},
            sw.Kind.NOTHING,
            c.NORMAL,
        ),
        # the variant only concerns holds
        ({"hold_no_pv": True, "target": c.CHARGE}, sw.Kind.SWITCH, c.CHARGE),
        (
            {"hold_no_pv": True, "target": c.NORMAL, "current": c.HOLD_NO_PV, "set_by_ems": c.HOLD_NO_PV},
            sw.Kind.SWITCH,
            c.NORMAL,
        ),
        # "Nur planen" takes back the own hold without PV, too
        (
            {"mode": c.PLAN_ONLY, "current": c.HOLD_NO_PV, "set_by_ems": c.HOLD_NO_PV, "trigger": "modus"},
            sw.Kind.SWITCH,
            c.NORMAL,
        ),
        # Forced mode + Stop that the EMS did not set is still foreign (e.g. during an own grid charge)
        (
            {
                "target": c.CHARGE,
                "current": c.HOLD_NO_PV,
                "set_by_ems": c.CHARGE,
                "inverter_changed_at": LONG_AGO,
            },
            sw.Kind.REPORT_FOREIGN,
            c.CHARGE,
        ),
    ],
)
def test_decide_hold_variant(changes, kind, goal):
    d = sw.decide(situation(**changes))
    assert d.kind is kind
    if kind is not sw.Kind.NOTHING:
        assert d.goal == goal


def test_commands_for_the_hold_without_pv():
    steps = sw.commands_for(c.HOLD_NO_PV, grid_kw=5, power_configured=True, normal_discharge_w=4000)
    assert [(x.kind, x.key, x.value) for x in steps] == [
        ("select", CONF_FORCED, c.FORCED_STOP),
        ("select", CONF_EMS_MODE, c.FORCED_MODE),
        ("number", CONF_DISCHARGE_MAX, 4000),
    ]


START = sw.HoldVariant()
DAY = sw.HoldVariant(False, LONG_AGO)
NIGHT = sw.HoldVariant(True, LONG_AGO)


@pytest.mark.parametrize(
    ("pv_w", "last", "no_pv"),
    [
        (0, START, True),  # at start the choice applies at once
        (300, START, False),
        (100, START, False),  # in the band at start: with PV (the default)
        (0, DAY, True),
        (49.9, DAY, True),
        (50, DAY, False),  # the band 50 … 150 W keeps what runs
        (149.9, DAY, False),
        (50, NIGHT, True),
        (149.9, NIGHT, True),
        (150, NIGHT, False),
        (None, NIGHT, False),  # PV unknown: hold with PV, it never wastes surplus
        (float("nan"), NIGHT, False),
        (float("inf"), NIGHT, False),
        (float("-inf"), NIGHT, False),
    ],
)
def test_hold_variant(pv_w, last, no_pv):
    assert sw.hold_variant(pv_w, last, NOW).no_pv is no_pv


def test_hold_variant_waits_before_changing_again():
    just = sw.HoldVariant(True, NOW - timedelta(seconds=sw.HOLD_VARIANT_MIN_S - 1))
    assert sw.hold_variant(2000, just, NOW) == just
    due = sw.HoldVariant(True, NOW - timedelta(seconds=sw.HOLD_VARIANT_MIN_S))
    assert sw.hold_variant(2000, due, NOW) == sw.HoldVariant(False, NOW)
    # no change: the time of the last change stays (it is not a heartbeat)
    assert sw.hold_variant(0, due, NOW) == due
