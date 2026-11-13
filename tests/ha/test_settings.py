"""Planner settings: which wear and grid charge power the plan and the switching use."""

from __future__ import annotations

import pytest

from custom_components.ems.const import CONF_GRID_CHARGE_POWER, CONF_GRID_KW, CONF_WEAR, CONF_WEAR_CT
from custom_components.ems.core.model import Battery
from custom_components.ems.inputs import Inputs
from custom_components.ems.settings import GRID_CHARGE, WEAR, NotChangeable, PlannerSettings

EXT = "input_number.external"


def settings(hass, conf=None, stored=None) -> PlannerSettings:
    s = PlannerSettings(conf or {}, Inputs(hass, conf or {}))
    s.bind(stored if stored is not None else {WEAR: 12.0, GRID_CHARGE: 4.0})
    return s


@pytest.mark.parametrize(
    ("conf", "external_state", "value", "fixed", "external"),
    [
        ({}, None, 12.0, False, None),  # the app value
        ({CONF_WEAR_CT: 8}, None, 8.0, True, None),  # fixed in YAML wins over the app
        ({CONF_WEAR: EXT}, "20", 20.0, False, EXT),  # an external entity wins over everything
        ({CONF_WEAR: EXT, CONF_WEAR_CT: 8}, "20", 20.0, True, EXT),
        ({CONF_WEAR: EXT}, "unavailable", 12.0, False, EXT),  # external without value: own value
        ({CONF_WEAR: EXT, CONF_WEAR_CT: 8}, "unavailable", 8.0, True, EXT),
    ],
)
def test_wear_precedence(hass, conf, external_state, value, fixed, external):
    if external_state is not None:
        hass.states.async_set(EXT, external_state)
    s = settings(hass, conf).wear()
    assert (s.value, s.fixed_in_yaml, s.external) == (value, fixed, external)


def test_defaults_without_stored_values(hass):
    s = settings(hass, stored={})
    assert s.wear().value == 10.0 and s.grid_charge().value == 5.0


def test_grid_charge_is_capped_wherever_it_comes_from(hass):
    hass.states.async_set(EXT, "7")
    assert settings(hass, {CONF_GRID_CHARGE_POWER: EXT}).grid_charge().value == 5.0
    assert settings(hass, {CONF_GRID_KW: 9}).grid_charge().value == 5.0
    s = settings(hass)
    s.set(GRID_CHARGE, 8.0)
    assert s.grid_charge().value == 5.0


def test_set_only_the_own_value(hass):
    stored = {WEAR: 12.0}
    s = settings(hass, stored=stored)
    s.set(WEAR, 15.0)
    assert stored[WEAR] == 15.0  # shared with the executor, which persists it
    with pytest.raises(NotChangeable):
        settings(hass, {CONF_WEAR_CT: 8}).set(WEAR, 15.0)
    hass.states.async_set(EXT, "20")
    with pytest.raises(NotChangeable):
        settings(hass, {CONF_WEAR: EXT}).set(WEAR, 15.0)


def test_battery_uses_the_settings(hass):
    b = settings(hass).battery()
    assert (b.wear_ct, b.grid_charge_kw) == (12.0, 4.0)
    assert b.capacity_kwh == Battery().capacity_kwh and b.soc_min == 0  # inverter values missing


def test_lower_bound_is_backup_reserve(hass):
    """battery_level already is the usable range: min SoC 5 % is 0 % there, the reserve applies."""
    conf = {"kapazitaet": "sensor.capacity", "reserve": "number.reserve"}
    hass.states.async_set("sensor.capacity", "10.13")
    hass.states.async_set("number.reserve", "5.0")
    b = settings(hass, conf).battery()
    assert b.capacity_kwh == 10.13 and b.soc_min == 0.05 and b.soc_max == 1


def test_load_profile_external_attribute_or_learned(hass):
    learned = {"load_profile": {"values": [400.0] * 24}}
    assert settings(hass, stored=learned).load_profile() == [400.0] * 24
    hass.states.async_set("sensor.profile", "500", {"profil": [500.0] * 24})
    conf = {"lastprofil": "sensor.profile"}
    assert settings(hass, conf, stored=learned).load_profile() == [500.0] * 24
