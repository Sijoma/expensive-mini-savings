"""The integration's view of Home Assistant states: which entity a config key points to, and its value
parsed. Coordinator, executor and recorder share one instance; none of them reads hass.states itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, State

from . import control as c
from .const import (
    CONF_CHARGED,
    CONF_CHARGED_PV,
    CONF_CONSUMED,
    CONF_DISCHARGE_MAX,
    CONF_EMS_MODE,
    CONF_FORCED,
    CONF_IMPORTED,
    CONF_SOC,
)
from .inverter import NO_VALUE, InverterSetting, classify
from .measurement import as_number
from .switching import NORMAL_DISCHARGE_W


@dataclass(frozen=True)
class InverterReading:
    """The three inverter settings the EMS switches, as read now."""

    mode: str | None
    forced: str | None
    discharge_w: float | None
    ready: bool
    """All three report a value."""
    changed_at: datetime | None
    """Last change of any of them."""

    @property
    def setting(self) -> InverterSetting:
        return classify(self.mode, self.forced, self.discharge_w)

    @property
    def state(self) -> str:
        """In the executor's terms (control.inverter_state)."""
        return c.inverter_state(self.mode, self.forced, self.discharge_w)

    @property
    def action_code(self) -> str:
        """As measured: the setting's code (n, h, l, f, e, v, x, a)."""
        return self.setting.value


class Inputs:
    def __init__(self, hass: HomeAssistant, conf: dict[str, Any]) -> None:
        self.hass = hass
        self.conf = conf

    # ---- generic ----

    def configured(self, key: str) -> bool:
        return bool(self.conf.get(key))

    def state(self, key: str) -> State | None:
        entity_id = self.conf.get(key)
        return self.hass.states.get(entity_id) if entity_id else None

    def value(self, key: str) -> str | None:
        st = self.state(key)
        return st.state if st else None

    def number(self, key: str) -> float | None:
        """The state as a number; None if missing, unavailable or not a number."""
        return as_number(self.value(key))

    def attribute(self, key: str, name: str) -> Any:
        st = self.state(key)
        return st.attributes.get(name) if st else None

    # ---- typed ----

    def soc(self) -> float | None:
        """Battery state of charge in % of the usable range."""
        return self.number(CONF_SOC)

    def consumed_kwh(self) -> float | None:
        """Total house consumption meter (for the load profile)."""
        return self.number(CONF_CONSUMED)

    def counters(self) -> c.Counters:
        """Meter readings for booking; missing meters count as 0."""
        return c.Counters(
            charged=self.number(CONF_CHARGED) or 0.0,
            charged_pv=self.number(CONF_CHARGED_PV) or 0.0,
            imported=self.number(CONF_IMPORTED) or 0.0,
        )

    def inverter(self) -> InverterReading:
        states = [self.state(k) for k in (CONF_EMS_MODE, CONF_FORCED, CONF_DISCHARGE_MAX)]
        values = [st.state if st else None for st in states]
        changed = [st.last_changed for st in states if st]
        return InverterReading(
            mode=values[0],
            forced=values[1],
            discharge_w=as_number(values[2]),
            ready=all(v not in NO_VALUE for v in values),
            changed_at=max(changed) if changed else None,
        )

    def inverter_entities(self) -> list[str]:
        return [e for k in (CONF_EMS_MODE, CONF_FORCED, CONF_DISCHARGE_MAX) if (e := self.conf.get(k))]

    def discharge_upper_w(self) -> int:
        """Normal discharge limit = the upper bound of the discharge limit entity."""
        upper = as_number(self.attribute(CONF_DISCHARGE_MAX, "max"))
        return int(upper) if upper else NORMAL_DISCHARGE_W
