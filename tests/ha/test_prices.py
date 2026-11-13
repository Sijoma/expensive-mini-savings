"""PriceSource: prices from a `preise` entity or the Tibber action, and the current price."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.ems.inputs import Inputs
from custom_components.ems.prices import PriceSource

NOW = datetime(2026, 11, 13, 22, 10, tzinfo=ZoneInfo("Europe/Berlin"))
SLOT = int(NOW.replace(minute=0).timestamp() * 1000)  # 22:00, the quarter hour of 22:10
PRICES = [[SLOT - 900_000, 30.0], [SLOT, 46.4849], [SLOT + 900_000, 50.0]]


def source(hass, conf) -> PriceSource:
    return PriceSource(hass, conf, Inputs(hass, conf))


def tibber(hass, fail: list):
    async def get_prices(call):
        if fail:
            raise HomeAssistantError("timeout")
        start = datetime.fromtimestamp(SLOT / 1000, ZoneInfo("Europe/Berlin"))
        return {"prices": {"Home": [{"start_time": start.isoformat(), "price": 0.4648}]}}

    hass.services.async_register("tibber", "get_prices", get_prices, supports_response=SupportsResponse.ONLY)


async def test_entity_adapter(hass):
    hass.states.async_set("sensor.prices", "3", {"preise": PRICES})
    s = source(hass, {"preise": "sensor.prices"})
    assert s.now_ct(NOW) == 46.48  # before the first fetch: read from the entity, rounded to 0.01 ct
    assert await s.refresh(NOW) == PRICES
    assert s.now_ct(NOW) == 46.48


async def test_tibber_adapter_and_its_last_good_prices(hass):
    fail: list = []
    tibber(hass, fail)
    s = source(hass, {})
    assert s.now_ct(NOW) is None  # before the first fetch Tibber cannot answer
    assert await s.refresh(NOW) == [[SLOT, 46.48]]
    assert s.now_ct(NOW) == 46.48
    fail.append(True)
    assert await s.refresh(NOW) == [[SLOT, 46.48]]  # Tibber fails: plan with the last ones


async def test_tibber_failing_before_it_ever_delivered(hass):
    tibber(hass, [True])
    with pytest.raises(UpdateFailed):
        await source(hass, {}).refresh(NOW)


async def test_no_source(hass):
    with pytest.raises(UpdateFailed):
        await source(hass, {}).refresh(NOW)


async def test_current_price_entity_wins(hass):
    hass.states.async_set("sensor.prices", "3", {"preise": PRICES})
    hass.states.async_set("sensor.price_now", "0.51234")
    s = source(hass, {"preise": "sensor.prices", "preis_jetzt": "sensor.price_now"})
    assert s.now_ct(NOW) == 51.23
    hass.states.async_set("sensor.price_now", "unavailable")
    assert s.now_ct(NOW) is None


async def test_current_price_is_the_last_fetch_not_the_last_plan(hass):
    """The current price does not depend on whether a plan could be made from the prices."""
    hass.states.async_set("sensor.prices", "3", {"preise": PRICES})
    s = source(hass, {"preise": "sensor.prices"})
    await s.refresh(NOW)
    assert s.now_ct(NOW + timedelta(minutes=15)) == 50.0
