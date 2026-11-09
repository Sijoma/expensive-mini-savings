"""Measurements per quarter hour (shared by the integration and the export)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ems.measurement import MeasurementWriter, action_from

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 11, 8, 10, 0, tzinfo=TZ)


def test_constant_values_one_quarter():
    m = MeasurementWriter(T0)
    for name, w in [("last", 2000), ("pv", 1000), ("akku", 500), ("netz", 500)]:
        m.value(name, T0, w)
    (v,) = m.boundary(T0 + timedelta(minutes=15))
    assert v["last_kw"] == 2.0 and v["pv_kw"] == 1.0
    assert v["bezug_brutto_kwh"] == pytest.approx(0.25)
    assert v["ueberschuss_brutto_kwh"] == 0
    assert v["akku_entladen_kwh"] == pytest.approx(0.125)
    assert v["netz_bezug_kwh"] == pytest.approx(0.125)
    assert v["abgedeckt_s"] == 900


def test_gross_flows_when_switching_within_quarter():
    """5 min consumption above PV, 10 min PV above consumption: surplus on average, both gross."""
    m = MeasurementWriter(T0)
    m.value("pv", T0, 2000)
    m.value("last", T0, 3000)
    m.value("last", T0 + timedelta(minutes=5), 500)
    (v,) = m.boundary(T0 + timedelta(minutes=15))
    assert v["bezug_brutto_kwh"] == pytest.approx(1.0 * 5 / 60, abs=1e-4)
    assert v["ueberschuss_brutto_kwh"] == pytest.approx(1.5 * 10 / 60, abs=1e-4)


def test_unavailable_keeps_last_value():
    m = MeasurementWriter(T0)
    m.value("last", T0, 1000)
    m.value("last", T0 + timedelta(minutes=5), "unavailable")
    (v,) = m.boundary(T0 + timedelta(minutes=15))
    assert v["last_kw"] == pytest.approx(1.0)


def test_start_mid_quarter_is_marked():
    m = MeasurementWriter(T0 + timedelta(minutes=10))
    m.value("last", T0 + timedelta(minutes=10), 1200)
    (v,) = m.boundary(T0 + timedelta(minutes=15))
    assert v["start"] == T0.isoformat()
    assert v["abgedeckt_s"] == 300


def test_several_boundaries_at_once():
    m = MeasurementWriter(T0)
    m.value("last", T0, 400)
    finished = m.value("last", T0 + timedelta(minutes=47), 800)
    assert [v["start"][11:16] for v in finished] == ["10:00", "10:15", "10:30"]
    assert all(v["last_kw"] == pytest.approx(0.4) for v in finished)


def test_extra_info_applies_to_current_quarter():
    m = MeasurementWriter(T0)
    m.extra_info(soc_percent=42.0, price_ct=47.1, action="h")
    (v,) = m.boundary(T0 + timedelta(minutes=15))
    assert (v["soc_prozent"], v["preis_ct"], v["aktion"]) == (42.0, 47.1, "h")
    (w,) = m.boundary(T0 + timedelta(minutes=30))
    assert w["soc_prozent"] is None and w["aktion"] == "n"


def test_dst_change_has_100_quarters():
    """2026-10-25 has 25 hours. Counting is in UTC, output in local time."""
    start = datetime(2026, 10, 25, 0, 0, tzinfo=TZ)
    m = MeasurementWriter(start)
    m.value("last", start, 1000)
    finished = m.boundary(datetime(2026, 10, 26, 0, 0, tzinfo=TZ))
    assert len(finished) == 100
    assert finished[0]["start"] == "2026-10-25T00:00:00+02:00"
    assert finished[-1]["start"] == "2026-10-25T23:45:00+01:00"
    assert sum(1 for v in finished if v["start"][11:16] == "02:15") == 2


@pytest.mark.parametrize(
    ("mode", "forced", "boundary", "expected"),
    [
        ("Self-consumption mode (default)", "Stop (default)", "5900", "n"),
        ("Self-consumption mode (default)", "Stop (default)", "10.0", "h"),
        ("Forced mode", "Forced charge", "5900", "l"),
        ("Forced mode", "Stop (default)", "5900", "f"),
        ("Forced mode", "Forced discharge", "5900", "e"),
        ("VPP", "Stop (default)", "5900", "v"),
        (None, None, "unavailable", "n"),
    ],
)
def test_action_from(mode, forced, boundary, expected):
    assert action_from(mode, forced, boundary) == expected


@settings(deadline=None)
@given(
    st.lists(
        st.tuples(
            st.integers(0, 2700), st.sampled_from(["last", "pv", "akku", "netz"]), st.integers(-6000, 9000)
        ),
        max_size=40,
    )
)
def test_matches_fine_sampling(events):
    """Cross-check: sample the step function every second and sum up."""
    events.sort()
    m = MeasurementWriter(T0)
    finished = []
    for s, name, w in events:
        finished += m.value(name, T0 + timedelta(seconds=s), w)
    finished += m.boundary(T0 + timedelta(minutes=45))
    assert len(finished) == 3
    values = {}
    total_sum = [dict.fromkeys(["last", "bezug", "entladen", "netz_e"], 0.0) for _ in range(3)]
    i = 0
    for sec in range(2700):
        while i < len(events) and events[i][0] <= sec:
            values[events[i][1]] = events[i][2]
            i += 1
        load, pv, battery, grid = (values.get(k, 0) / 1000 for k in ["last", "pv", "akku", "netz"])
        z = total_sum[sec // 900]
        z["last"] += load / 3600
        z["bezug"] += max(load - pv, 0) / 3600
        z["entladen"] += max(battery, 0) / 3600
        z["netz_e"] += max(-grid, 0) / 3600
    for v, z in zip(finished, total_sum, strict=True):
        assert v["last_kw"] * 0.25 == pytest.approx(z["last"], abs=2e-3)
        assert v["bezug_brutto_kwh"] == pytest.approx(z["bezug"], abs=1e-4)
        assert v["akku_entladen_kwh"] == pytest.approx(z["entladen"], abs=1e-4)
        assert v["netz_einspeisung_kwh"] == pytest.approx(z["netz_e"], abs=1e-4)
