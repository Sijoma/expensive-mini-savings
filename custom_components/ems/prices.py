"""Prices for the planner and the current price, from one of two adapters: the attribute `preise` of a
configured entity, or the Home Assistant action tibber.get_prices. The current price comes from an optional
`preis_jetzt` entity (EUR/kWh) or the quarter hour of the last fetched list.

The pure translations (Tibber response → [[ms, ct], …], the quarter hour's price) live in adapter.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from .adapter import ct_from_eur, price_at, prices_from_tibber
from .const import CONF_PRICE_NOW, CONF_PRICES
from .inputs import Inputs

_LOGGER = logging.getLogger(__name__)

TIBBER = ("tibber", "get_prices")
"""The Tibber integration keeps the prices and only asks Tibber again when today's or (from 13:00)
tomorrow's are missing, so calling it on every replan is cheap."""


class PriceSource:
    def __init__(self, hass: HomeAssistant, conf: dict[str, Any], inputs: Inputs) -> None:
        self.hass = hass
        self.conf = conf
        self.inputs = inputs
        self._latest: list[list[float]] | None = None
        """The last fetched list (also when the plan built from it failed)."""
        self._tibber_good: list[list[float]] | None = None
        """The last list Tibber delivered, used while the action fails."""

    async def refresh(self, now: datetime) -> list[list[float]] | None:
        """Today's and tomorrow's prices [[ms, ct], …]. Raises UpdateFailed when there is no source or
        Tibber fails before it ever delivered."""
        if self.conf.get(CONF_PRICES):
            self._latest = self.inputs.attribute(CONF_PRICES, "preise")
            return self._latest
        if not self.hass.services.has_service(*TIBBER):
            raise UpdateFailed("no prices: set `preise` or set up the Tibber integration")
        start = dt_util.start_of_local_day(now)
        try:
            response = await self.hass.services.async_call(
                *TIBBER,
                {"start": start.isoformat(), "end": (start + timedelta(days=2)).isoformat()},
                blocking=True,
                return_response=True,
            )
        except HomeAssistantError as err:
            if self._tibber_good is None:
                raise UpdateFailed(f"no prices from Tibber: {err}") from err
            _LOGGER.warning("Tibber prices not updated, planning with the last ones: %s", err)
            self._latest = self._tibber_good
            return self._latest
        self._tibber_good = self._latest = prices_from_tibber(response)
        return self._latest

    def now_ct(self, now: datetime | None = None) -> float | None:
        """Current price in ct/kWh (0.01 ct): `preis_jetzt` if configured, else the quarter hour of the last
        fetched list. Before the first fetch only the entity adapter can answer (with Tibber: None, i.e. the
        first quarter hour after a Home Assistant start has no price)."""
        if self.conf.get(CONF_PRICE_NOW):
            price = self.inputs.number(CONF_PRICE_NOW)
            return ct_from_eur(price) if price is not None else None
        prices = self._latest
        if prices is None and self.conf.get(CONF_PRICES):
            prices = self.inputs.attribute(CONF_PRICES, "preise")
        price = price_at(prices, now or dt_util.now())
        return round(price, 2) if price is not None else None
