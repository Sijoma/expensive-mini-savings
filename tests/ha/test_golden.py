"""Golden test of the interface the EMS app (frontend) reads: state and attributes of every entity of the
integration and the `ems/config` payload, for the example inputs at a fixed time. Any change to a key or a
value fails here; if the change is intended, regenerate and review the diff:

    UPDATE_GOLDEN=1 uv run --group ha pytest tests/ha/test_golden.py -o addopts=""
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from test_integration import ENTITIES

from custom_components.ems.panel import app_config

GOLDEN = Path(__file__).parent / "golden" / "app_interface.json"
VOLATILE = {"rechenzeit_ms": "<ms>", "version": "<version>", "eintrag": "<entry id>"}
"""Values that differ per run or per release; their keys stay part of the interface."""


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: VOLATILE.get(k, _normalize(v)) if k in VOLATILE else _normalize(v) for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_normalize(v) for v in value]
    if isinstance(value, float):
        return round(value, 6)
    return value


def _snapshot(hass) -> dict[str, Any]:
    registry = er.async_get(hass)
    entities = {}
    for entry in sorted(registry.entities.values(), key=lambda e: e.entity_id):
        if entry.platform != "ems":
            continue
        state = hass.states.get(entry.entity_id)
        entities[entry.entity_id] = (
            None if state is None else {"state": state.state, "attributes": dict(state.attributes)}
        )
    return json.loads(
        json.dumps(
            _normalize({"entities": entities, "ems/config": app_config(hass)}),
            default=str,
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@pytest.fixture
async def ems(hass, freezer, set_states, now, tmp_path):
    hass.config.config_dir = str(tmp_path)
    freezer.move_to(now)
    await hass.config.async_set_time_zone("Europe/Berlin")
    set_states(**{"sensor.battery_level": "45.0"})
    assert await async_setup_component(hass, "ems", {"ems": {**ENTITIES, "aufzeichnung": False}})
    await hass.async_block_till_done()
    return hass


async def test_app_interface_is_unchanged(ems):
    snapshot = _snapshot(ems)
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(snapshot, indent=1, ensure_ascii=False, sort_keys=True) + "\n")
    golden = json.loads(GOLDEN.read_text())
    assert snapshot["ems/config"] == golden["ems/config"]
    assert sorted(snapshot["entities"]) == sorted(golden["entities"])
    for entity_id, expected in golden["entities"].items():
        assert snapshot["entities"][entity_id] == expected, entity_id
