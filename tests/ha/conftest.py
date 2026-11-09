"""Tests of the HA integration in a real (test) Home Assistant (pytest-homeassistant-custom-component).

uv run --group ha pytest tests/ha -o addopts=""
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

import custom_components  # package of the HA test package (testing_config/custom_components)

EXAMPLE = Path(__file__).parent.parent / "example"
OURS = str(Path(__file__).resolve().parents[2] / "custom_components")
# HA looks for integrations in custom_components.__path__; the test package ships its own folder
if OURS not in custom_components.__path__:
    custom_components.__path__.append(OURS)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    return


@pytest.fixture
def ha_states() -> dict:
    return json.loads((EXAMPLE / "ha_eingaben.json").read_text())["zustaende"]


@pytest.fixture
def set_states(hass, ha_states):
    def apply(**overrides):
        for eid, z in ha_states.items():
            state = overrides.get(eid, z["state"])
            if state is None:
                continue
            hass.states.async_set(eid, state, z["attributes"])

    return apply


@pytest.fixture
def now() -> datetime:
    return datetime.fromisoformat("2026-11-08T22:31:07+01:00")
