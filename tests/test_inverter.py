"""One classification of the inverter setting: the executor's view and the measured code always agree."""

from __future__ import annotations

from itertools import product

import pytest

from custom_components.ems import control as c
from custom_components.ems.inverter import InverterSetting, classify
from custom_components.ems.measurement import action_from

MODES = [c.SELF_CONSUMPTION, c.FORCED_MODE, "VPP", "External EMS mode", None, "unavailable"]
FORCED = [c.FORCED_STOP, c.FORCED_CHARGE, "Forced discharge", None, "unknown"]
DISCHARGE = [5900.0, 101.0, 100.0, 10.0, 0.0, -5.0, None]

EXECUTOR_TERM = {"n": c.NORMAL, "h": c.HOLD, "l": c.CHARGE, "x": c.UNREACHABLE}


@pytest.mark.parametrize(("mode", "forced", "discharge"), list(product(MODES, FORCED, DISCHARGE)))
def test_executor_view_and_measured_code_agree(mode, forced, discharge):
    code = action_from(mode, forced, discharge)
    state = c.inverter_state(mode, forced, discharge)
    if code in EXECUTOR_TERM:
        assert state == EXECUTOR_TERM[code]
    else:
        assert state.startswith("Anders (")


@pytest.mark.parametrize(
    ("mode", "forced", "discharge", "setting"),
    [
        (c.SELF_CONSUMPTION, c.FORCED_STOP, 5900.0, InverterSetting.NORMAL),
        (c.SELF_CONSUMPTION, c.FORCED_STOP, 100.0, InverterSetting.HOLD),
        (c.SELF_CONSUMPTION, c.FORCED_STOP, 0.0, InverterSetting.HOLD),
        (c.SELF_CONSUMPTION, c.FORCED_STOP, 0.5, InverterSetting.HOLD),
        (c.SELF_CONSUMPTION, c.FORCED_STOP, None, InverterSetting.UNREACHABLE),
        (c.SELF_CONSUMPTION, c.FORCED_CHARGE, 10.0, InverterSetting.OTHER),
        (c.FORCED_MODE, c.FORCED_CHARGE, 5900.0, InverterSetting.CHARGE),
        (c.FORCED_MODE, c.FORCED_STOP, 5900.0, InverterSetting.OLD_HOLD),
        (c.FORCED_MODE, "Forced discharge", 5900.0, InverterSetting.FORCED_DISCHARGE),
        ("VPP", c.FORCED_STOP, 5900.0, InverterSetting.VPP),
        ("unavailable", c.FORCED_STOP, 5900.0, InverterSetting.UNREACHABLE),
    ],
)
def test_classify(mode, forced, discharge, setting):
    assert classify(mode, forced, discharge) is setting
