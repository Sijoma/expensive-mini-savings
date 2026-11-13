"""The planner's settings: battery wear and grid charge power, and the load profile.

One place answers "which value does the plan use?". Precedence: an external entity configured in the
integration (verschleiss / netzladeleistung) > a value fixed in configuration.yaml (verschleiss_ct /
netzladeleistung_kw) > the value set in the app (stored by the executor) > the default. An external entity
without a value falls back to the own value. The planner and the switching at the inverter read the same
values. The load profile: the external entity's attribute `profil` if present, else the learned one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_CAPACITY,
    CONF_GRID_CHARGE_POWER,
    CONF_GRID_KW,
    CONF_LOAD_PROFILE,
    CONF_RESERVE,
    CONF_WEAR,
    CONF_WEAR_CT,
    GRID_KW_DEFAULT,
    GRID_KW_MAX,
    GRID_KW_MIN,
    WEAR_CT_DEFAULT,
    WEAR_CT_MAX,
    WEAR_CT_MIN,
)
from .core.model import Battery
from .inputs import Inputs
from .measurement import as_number

WEAR = "wear_ct"
GRID_CHARGE = "grid_charge_kw"
"""Keys of the own values in the executor's stored state (unchanged since 0.6)."""


@dataclass(frozen=True)
class Limits:
    entity_key: str
    yaml_key: str
    default: float
    minimum: float
    maximum: float
    capped: bool
    """The value is capped at `maximum` wherever it comes from (grid charge power: inverter limit)."""


LIMITS = {
    WEAR: Limits(CONF_WEAR, CONF_WEAR_CT, WEAR_CT_DEFAULT, WEAR_CT_MIN, WEAR_CT_MAX, capped=False),
    GRID_CHARGE: Limits(
        CONF_GRID_CHARGE_POWER, CONF_GRID_KW, GRID_KW_DEFAULT, GRID_KW_MIN, GRID_KW_MAX, capped=True
    ),
}


@dataclass(frozen=True)
class Setting:
    value: float
    """The effective value the plan and the switching use."""
    fixed_in_yaml: bool
    external: str | None
    """Entity ID of an external entity that overrides the own value."""

    @property
    def changeable(self) -> bool:
        """Can be set in the app (neither fixed in YAML nor overridden by an external entity)."""
        return not self.fixed_in_yaml and self.external is None


class NotChangeable(Exception):
    """The setting is fixed in configuration.yaml or overridden by an external entity."""


class PlannerSettings:
    def __init__(self, conf: dict[str, Any], inputs: Inputs) -> None:
        self.conf = conf
        self.inputs = inputs
        self._stored: dict[str, Any] = {}

    def bind(self, stored: dict[str, Any]) -> None:
        """The executor's loaded state: own values (wear_ct, grid_charge_kw) and the learned load profile.
        Shared, not copied: the executor persists it."""
        self._stored = stored

    def _own(self, key: str) -> float:
        lim = LIMITS[key]
        fixed = as_number(self.conf.get(lim.yaml_key))
        if fixed is not None:
            return fixed
        return as_number(self._stored.get(key), lim.default)

    def get(self, key: str) -> Setting:
        lim = LIMITS[key]
        external = self.conf.get(lim.entity_key) or None
        value = self.inputs.number(lim.entity_key) if external else None
        if value is None:
            value = self._own(key)
        if lim.capped:
            value = min(value, lim.maximum)
        return Setting(value, as_number(self.conf.get(lim.yaml_key)) is not None, external)

    def wear(self) -> Setting:
        return self.get(WEAR)

    def grid_charge(self) -> Setting:
        return self.get(GRID_CHARGE)

    def set(self, key: str, value: float) -> None:
        """Set an own value (from the app). Raises NotChangeable if fixed in YAML or overridden."""
        if not self.get(key).changeable:
            raise NotChangeable(key)
        lim = LIMITS[key]
        self._stored[key] = min(value, lim.maximum) if lim.capped else value

    def load_profile(self) -> list[float]:
        """Expected consumption per hour of day (W): the external attribute if present, else learned."""
        external = self.inputs.attribute(CONF_LOAD_PROFILE, "profil")
        if external is not None:
            return external
        return list((self._stored.get("load_profile") or {}).get("values") or [])

    def battery(self) -> Battery:
        """The battery as planned: capacity and reserve from the inverter, wear and grid charge power."""
        default = Battery()
        capacity = self.inputs.number(CONF_CAPACITY)
        reserve = self.inputs.number(CONF_RESERVE)
        return Battery(
            capacity_kwh=capacity if capacity is not None else default.capacity_kwh,
            soc_min=min(max(reserve if reserve is not None else 0.0, 0.0), 100.0) / 100,
            wear_ct=self.wear().value,
            grid_charge_kw=self.grid_charge().value,
        )
