"""Heat pump PV boost without Home Assistant: the rules of the former automation, and the decisions."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.ems import heat_pump_control as hp
from custom_components.ems.const import CONF_WP_BLOCK, CONF_WP_BOOST, CONF_WP_SG_READY
from custom_components.ems.switching import FOREIGN_REPEAT_S, ForeignReport

TZ = ZoneInfo("Europe/Berlin")
NOON = datetime(2026, 11, 17, 12, 0, tzinfo=TZ)
LONG_AGO = NOON - timedelta(days=1)

# Sunny noon, battery full, exporting 2 kW for 15 min: everything a boost needs
SUNNY = hp.Readings(
    now=NOON,
    grid_w=-2000,
    battery_w=0,
    soc=95,
    boosted=False,
    blocked=False,
    boosted_changed_at=LONG_AGO,
    operating_mode="program",
    operating_mode_required="program",
)
EXPORTING = hp.Timers(export_high=NOON - timedelta(minutes=15))


def at(hh: int, mm: int = 0) -> datetime:
    return NOON.replace(hour=hh, minute=mm)


def want(r: hp.Readings = SUNNY, t: hp.Timers = EXPORTING) -> bool:
    return hp.rules(r, t)[0]


# ---- timers ----


def test_timers_start_hold_and_reset():
    t = hp.advance(hp.Timers(), SUNNY)
    assert t.export_high == NOON and t.import_high is None
    later = replace(SUNNY, now=NOON + timedelta(minutes=3))
    assert hp.advance(t, later).export_high == NOON  # still high: keeps its start
    dip = replace(later, grid_w=-1000)
    assert hp.advance(t, dip).export_high is None  # one value below interrupts


@pytest.mark.parametrize(
    ("grid_w", "battery_w", "field"),
    [
        (-1201, 0, "export_high"),
        (301, 0, "import_high"),
        (0, -2501, "charge_high"),
        (0, 301, "discharge_high"),
    ],
)
def test_timer_thresholds_are_strict(grid_w, battery_w, field):
    """Like the automation's numeric_state "above": exactly at the threshold is not above."""
    above = hp.advance(hp.Timers(), replace(SUNNY, grid_w=grid_w, battery_w=battery_w))
    assert getattr(above, field) == NOON
    at_limit = {
        "export_high": (-1200, 0),
        "import_high": (300, 0),
        "charge_high": (0, -2500),
        "discharge_high": (0, 300),
    }[field]
    exactly = hp.advance(hp.Timers(), replace(SUNNY, grid_w=at_limit[0], battery_w=at_limit[1]))
    assert getattr(exactly, field) is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), float("-inf")])
def test_missing_power_never_counts_as_high(bad):
    t = hp.advance(EXPORTING, replace(SUNNY, grid_w=bad, battery_w=bad))
    assert t == hp.Timers()


# ---- on ----


@pytest.mark.parametrize(
    ("changes", "timers", "on"),
    [
        ({}, EXPORTING, True),
        # export or strong charging must have lasted 10 min
        ({}, hp.Timers(export_high=NOON - timedelta(minutes=9, seconds=59)), False),
        ({}, hp.Timers(export_high=NOON - timedelta(minutes=10)), True),
        ({}, hp.Timers(charge_high=NOON - timedelta(minutes=10)), True),
        ({}, hp.Timers(), False),
        # only 09:00–16:30
        ({"now": at(8, 59)}, EXPORTING, False),
        ({"now": at(9, 0)}, hp.Timers(export_high=at(8, 45)), True),
        ({"now": at(16, 29)}, hp.Timers(export_high=at(16, 0)), True),
        ({"now": at(16, 30)}, hp.Timers(export_high=at(16, 0)), False),
        # battery above 80 %
        ({"soc": 80}, EXPORTING, False),
        ({"soc": 80.1}, EXPORTING, True),
        ({"soc": None}, EXPORTING, False),
        # operating mode
        ({"operating_mode": "comfort"}, EXPORTING, False),
        ({"operating_mode": None}, EXPORTING, False),
        ({"operating_mode": "comfort", "operating_mode_required": None}, EXPORTING, True),
        # 30 min pause after the last boost
        ({"boosted_changed_at": NOON - timedelta(minutes=29)}, EXPORTING, False),
        ({"boosted_changed_at": NOON - timedelta(minutes=30)}, EXPORTING, True),
        ({"boosted_changed_at": None}, EXPORTING, True),
    ],
)
def test_on_rules(changes, timers, on):
    assert want(replace(SUNNY, **changes), timers) is on


# ---- off ----

BOOSTED = replace(SUNNY, boosted=True)


@pytest.mark.parametrize(
    ("changes", "timers", "on"),
    [
        ({}, hp.Timers(), True),  # a running boost needs no ongoing export, only no import
        ({}, hp.Timers(import_high=NOON - timedelta(minutes=5)), False),
        ({}, hp.Timers(import_high=NOON - timedelta(minutes=4, seconds=59)), True),
        ({}, hp.Timers(discharge_high=NOON - timedelta(minutes=5)), False),
        ({"now": at(16, 59)}, hp.Timers(), True),
        ({"now": at(17, 0)}, hp.Timers(), False),
        ({"now": at(23, 0)}, hp.Timers(), False),
        ({"now": at(3, 0)}, hp.Timers(), False),  # never keeps an own boost over night
        ({"soc": 50}, hp.Timers(), True),  # SoC only matters for starting
    ],
)
def test_off_rules(changes, timers, on):
    assert want(replace(BOOSTED, **changes), timers) is on


def test_every_rule_has_a_reason():
    assert hp.rules(SUNNY, EXPORTING)[1] == "Seit 10 min mehr als 1200 W Einspeisung"
    assert "9:00" in hp.rules(replace(SUNNY, now=at(8)), hp.Timers(export_high=at(7, 30)))[1]
    assert "80 %" in hp.rules(replace(SUNNY, soc=70), EXPORTING)[1]
    assert "17 Uhr" in hp.rules(replace(BOOSTED, now=at(17)), hp.Timers())[1]


# ---- decisions ----

BASE = hp.Situation(mode=hp.AUTOMATIC, readings=SUNNY, timers=EXPORTING, set_by_ems=False, trigger="pruefen")


def situation(**changes) -> hp.Situation:
    return replace(BASE, **changes)


@pytest.mark.parametrize(
    ("changes", "kind", "on"),
    [
        ({}, hp.Kind.SWITCH, True),
        # own boost, the import comes: off
        (
            {
                "readings": BOOSTED,
                "set_by_ems": True,
                "timers": hp.Timers(import_high=NOON - timedelta(minutes=6)),
            },
            hp.Kind.SWITCH,
            False,
        ),
        ({"readings": BOOSTED, "set_by_ems": True}, hp.Kind.NOTHING, False),
        # nothing in "Nur planen" ...
        ({"mode": hp.PLAN_ONLY}, hp.Kind.NOTHING, False),
        ({"mode": hp.PLAN_ONLY, "readings": BOOSTED, "set_by_ems": True}, hp.Kind.NOTHING, False),
        # ... except taking back the own boost when the mode changes
        (
            {"mode": hp.PLAN_ONLY, "readings": BOOSTED, "set_by_ems": True, "trigger": "modus"},
            hp.Kind.SWITCH,
            False,
        ),
        ({"mode": hp.PLAN_ONLY, "readings": BOOSTED, "trigger": "modus"}, hp.Kind.NOTHING, False),
        # a boost the EMS did not set, or a block: reported, never touched
        ({"readings": BOOSTED}, hp.Kind.REPORT_FOREIGN, False),
        ({"readings": replace(SUNNY, blocked=True)}, hp.Kind.REPORT_FOREIGN, False),
        ({"readings": replace(BOOSTED, blocked=True), "set_by_ems": True}, hp.Kind.REPORT_FOREIGN, False),
        ({"readings": replace(SUNNY, blocked=True), "switch_failed": True}, hp.Kind.REPORT_FOREIGN, False),
        # ... unless the own last switch failed: then a boost may be the EMS's half-done command
        (
            {
                "readings": BOOSTED,
                "switch_failed": True,
                "timers": hp.Timers(import_high=NOON - timedelta(minutes=6)),
            },
            hp.Kind.SWITCH,
            False,
        ),
        # the own boost was switched off by someone else: forget it
        ({"set_by_ems": True}, hp.Kind.OWN_ENDED, False),
        ({"set_by_ems": True, "mode": hp.PLAN_ONLY}, hp.Kind.OWN_ENDED, False),
        # no values: nothing
        ({"readings": replace(SUNNY, boosted=None)}, hp.Kind.NOTHING, False),
        ({"readings": replace(SUNNY, blocked=None)}, hp.Kind.NOTHING, False),
    ],
)
def test_decide(changes, kind, on):
    d = hp.decide(situation(**changes))
    assert d.kind is kind
    if kind is hp.Kind.SWITCH:
        assert d.on is on


def test_foreign_setting_is_reported_once_per_12_hours():
    blocked = replace(SUNNY, blocked=True)
    recent = ForeignReport(hp.BLOCKED, NOON - timedelta(seconds=FOREIGN_REPEAT_S))
    assert hp.decide(situation(readings=blocked, foreign_reported=recent)).kind is hp.Kind.NOTHING
    old = ForeignReport(hp.BLOCKED, NOON - timedelta(seconds=FOREIGN_REPEAT_S + 1))
    assert hp.decide(situation(readings=blocked, foreign_reported=old)).kind is hp.Kind.REPORT_FOREIGN
    other = ForeignReport(hp.BOOST, NOON)
    assert hp.decide(situation(readings=blocked, foreign_reported=other)).kind is hp.Kind.REPORT_FOREIGN


def test_state_names():
    assert hp.state_of(SUNNY) == hp.NORMAL
    assert hp.state_of(BOOSTED) == hp.BOOST
    assert hp.state_of(replace(BOOSTED, blocked=True)) == hp.BLOCKED
    assert hp.state_of(replace(SUNNY, boosted=None)) == hp.UNREACHABLE


def test_commands_in_order():
    def plain(steps):
        return [(x.key, x.value) for x in steps]

    assert plain(hp.commands(True, sg_ready=True, block=True)) == [
        (CONF_WP_BLOCK, False),
        (CONF_WP_SG_READY, True),
        (CONF_WP_BOOST, True),
    ]
    assert plain(hp.commands(False, sg_ready=True, block=True)) == [
        (CONF_WP_BOOST, False),
        (CONF_WP_BLOCK, False),
    ]
    assert plain(hp.commands(True, sg_ready=False, block=False)) == [(CONF_WP_BOOST, True)]
    assert plain(hp.commands(False, sg_ready=False, block=False)) == [(CONF_WP_BOOST, False)]
