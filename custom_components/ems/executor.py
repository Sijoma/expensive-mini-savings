"""The executor: runs the plan at the inverter (modes, approvals, push, booking, log, load profile).

Rules are in control.py (no HA). This module wires them to Home Assistant: it reads the plan from the
coordinator, switches the inverter through its entities, asks for approval by push, books savings and keeps
a log. State is stored in .storage (ems.steuerung) and survives restarts.

Former YAML executor (up to 0.5): on the first start, /config/ems/uebernahme.json (written by the author's
takeover script) is imported once: mode, approval, running block, log, savings, forecast, load profile.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import archive
from . import control as c
from . import switching as sw
from .adapter import PlanBlock
from .const import (
    CONF_FORCED_POWER,
    CONF_NOTIFY,
    DOMAIN,
    GRID_KW_DEFAULT,
    SIGNAL_EXECUTOR,
    WEAR_CT_DEFAULT,
)
from .inputs import Inputs
from .measurement import as_number
from .settings import NotChangeable, PlannerSettings

if TYPE_CHECKING:
    from .coordinator import EmsCoordinator

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
STORE_KEY = f"{DOMAIN}.steuerung"
TAKEOVER_FILE = ("ems", "uebernahme.json")
CONFIRM_S = 60
"""Every command is checked after this long."""
LOG_MAX = 50
FORECAST_DAYS = 100
SAVINGS_MAX = 100
NOTIFY_TAG = "ems-akku-freigabe"
ACTION_YES = "EMS_AKKU_JA"
ACTION_NO = "EMS_AKKU_NEIN"
APP_URL = "/ems"
LEGACY_AUTOMATION = "automation.ems_akku_steuern"
"""The former YAML executor (up to 0.5)."""


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


def _eur(v: float) -> str:
    return f"{v:.2f}".replace(".", ",") + " €"


def empty_state() -> dict[str, Any]:
    today = dt_util.now().date().isoformat()
    return {
        "mode": c.PLAN_ONLY,
        "approval": c.Approval().as_dict(),
        "set_by_ems": c.NORMAL,
        "running_block": None,
        "log": [],
        "savings": {"value": 0.0, "items": [], "since": today},
        "forecast": {"value": 0.0, "last_day": None, "since": today, "days": []},
        "load_profile": {"values": list(c.START_PROFILE), "meter": None, "at": None},
        "wear_ct": WEAR_CT_DEFAULT,
        "grid_charge_kw": GRID_KW_DEFAULT,
        "requested_at": None,
        "switch_failed": False,
        "foreign_reported": None,
    }


def _switch_memory_from_log(stored: dict[str, Any]) -> dict[str, Any]:
    """Up to 0.7 the log was the switching memory: a newest "Fehler" entry allowed a retry, the last
    "Manuell" entry told which foreign setting was reported when. Derive the explicit fields once."""
    log = stored.get("log") or []
    memory: dict[str, Any] = {"switch_failed": bool(log) and log[0].get("art") == "Fehler"}
    manual = next((e for e in log if e.get("art") == "Manuell"), None)
    match = re.search(r"auf „(.+?)“", manual.get("text", "")) if manual else None
    at = dt_util.parse_datetime(manual.get("zeit") or "") if manual else None
    memory["foreign_reported"] = sw.ForeignReport(match[1], at).as_dict() if match and at else None
    return memory


def from_takeover(data: dict[str, Any]) -> dict[str, Any]:
    """State from the former YAML executor (same keys as the stored state, written by the takeover script)."""
    state = empty_state()
    for key in ("mode", "set_by_ems", "running_block", "log", "wear_ct", "grid_charge_kw"):
        if data.get(key) is not None:
            state[key] = data[key]
    if data.get("approval"):
        state["approval"] = c.Approval.from_dict(data["approval"]).as_dict()
    for key in ("savings", "forecast", "load_profile"):
        if isinstance(data.get(key), dict):
            state[key].update({k: v for k, v in data[key].items() if v is not None})
    if state["mode"] not in c.MODES:
        state["mode"] = c.PLAN_ONLY
    return state


class Executor:
    def __init__(
        self,
        hass: HomeAssistant,
        conf: dict[str, Any],
        coordinator: EmsCoordinator,
        inputs: Inputs,
        settings: PlannerSettings,
        price_now_ct: Callable[[], float | None],
    ) -> None:
        self.hass = hass
        self.conf = conf
        self.coordinator = coordinator
        self.inputs = inputs
        self.settings = settings
        self._price_now_ct = price_now_ct
        """Current price in ct/kWh (PriceSource.now_ct)."""
        self.store: Store[dict[str, Any]] = Store(hass, STORE_VERSION, STORE_KEY)
        self.state = empty_state()
        settings.bind(self.state)
        self.target = c.NORMAL
        self.target_since = dt_util.now()
        self.is_pending = False
        self.pending_since: datetime | None = None
        self._lock = asyncio.Lock()
        self._unsub: list[Callable[[], None]] = []
        self._warned_yaml = False
        self.target_entity_id: str | None = None
        self.archive_folder = Path(hass.config.path("ems"))
        self._backfilled_at: str | None = None
        """Set by the target sensor; used for logbook entries."""

    # ---- persistence ----

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if stored:
            self.state = {**empty_state(), **_switch_memory_from_log(stored), **stored}
        else:
            path = Path(self.hass.config.path(*TAKEOVER_FILE))
            data = await self.hass.async_add_executor_job(_read_takeover, path)
            if data:
                self.state = from_takeover(data)
                self._backfill_archive()
                self.log("Modus", "Steuerung von der YAML-Automation in die Integration übernommen")
                _LOGGER.info("EMS: state of the former YAML executor taken over from %s", path)
            self._save()
        if not self.state.get("archive"):
            self._backfill_archive()
        if self.state["archive"].get("since") == self._backfilled_at:
            await self.store.async_save(self.state)  # right away: a reload must not backfill twice
        self.target = self.state.get("set_by_ems") or c.NORMAL
        self.settings.bind(self.state)

    def _archive(self, kind: str, *entries: dict[str, Any]) -> None:
        self.hass.async_add_executor_job(archive.append, self.archive_folder, kind, list(entries))

    def _backfill_archive(self) -> None:
        """First start with the archive: write what the store already holds (oldest first)."""
        self._archive(archive.LOG, *reversed(self.state["log"]))
        self._archive(archive.SAVINGS, *reversed(self.state["savings"]["items"]))
        self._archive(archive.FORECAST, *self.state["forecast"].get("days", []))
        self._backfilled_at = dt_util.now().isoformat(timespec="seconds")
        self.state["archive"] = {"since": self._backfilled_at}
        self._save()

    def _save(self) -> None:
        self.store.async_delay_save(lambda: self.state, 2)

    def _changed(self) -> None:
        self._save()
        async_dispatcher_send(self.hass, SIGNAL_EXECUTOR)

    # ---- inputs ----

    @property
    def mode(self) -> str:
        return self.state["mode"]

    @property
    def approval(self) -> c.Approval:
        return c.Approval.from_dict(self.state["approval"])

    def inverter(self) -> str:
        return self.inputs.inverter().state

    def price_ct(self) -> float | None:
        price = self._price_now_ct()
        return round(price, 1) if price is not None else None  # 0.1 ct is enough for the log

    def offered(self) -> list[PlanBlock]:
        return c.offered(self.coordinator.latest_blocks(), _ms(dt_util.now()))

    # ---- start / stop ----

    @callback
    def start(self) -> None:
        h = self.hass
        self._unsub += [
            self.coordinator.async_add_listener(self._plan_updated),
            async_track_time_change(h, self._every_minute, second=5),
            async_track_time_change(h, self._check, minute=list(range(0, 60, 5)), second=20),
            async_track_time_change(h, self._forecast, hour=14, minute=5, second=0),
            async_track_time_change(h, self._learn, minute=0, second=10),
            h.bus.async_listen("mobile_app_notification_action", self._notification_action),
        ]
        self._unsub.append(
            async_track_state_change_event(h, self.inputs.inverter_entities(), self._inverter_changed)
        )
        self.evaluate()
        self._remember_meter()
        async_dispatcher_send(h, SIGNAL_EXECUTOR)
        h.async_create_task(self.apply("start"))

    @callback
    def stop(self) -> None:
        for unsub in self._unsub:
            unsub()
        self._unsub = []

    # ---- target and approvals ----

    @callback
    def evaluate(self) -> None:
        """Recompute target and pending approvals; switch if the target changed."""
        if self.coordinator.data is None:
            # no plan yet since start: keep what runs (no switching back and forth during a restart)
            return
        now = dt_util.now()
        blocks = self.coordinator.latest_blocks()
        new = c.target(
            self.mode, blocks, _ms(now), self.coordinator.plan_fresh(now), self.approval, self.target,
            self.inputs.soc(),
        )  # fmt: skip
        pending = c.pending(self.mode, blocks, _ms(now), self.approval)
        if pending and not self.is_pending:
            self.pending_since = now
        if not pending:
            self.pending_since = None
        changed = new != self.target or pending != self.is_pending
        self.is_pending = pending
        if new != self.target:
            self.target = new
            self.target_since = now
            self.hass.async_create_task(self.apply("soll"))
        if changed:
            async_dispatcher_send(self.hass, SIGNAL_EXECUTOR)

    @callback
    def _plan_updated(self) -> None:
        self.evaluate()
        async_dispatcher_send(self.hass, SIGNAL_EXECUTOR)

    @callback
    def _inverter_changed(self, _event: Event) -> None:
        async_dispatcher_send(self.hass, SIGNAL_EXECUTOR)

    @callback
    def _every_minute(self, now: datetime) -> None:
        self.evaluate()
        async_dispatcher_send(self.hass, SIGNAL_EXECUTOR)
        self.hass.async_create_task(self._maybe_request(now))

    @callback
    def _check(self, _now: datetime) -> None:
        self.hass.async_create_task(self.apply("pruefen"))

    async def set_mode(self, mode: str) -> None:
        if mode not in c.MODES or mode == self.mode:
            return
        old = self.mode
        self.state["mode"] = mode
        self.log("Modus", f"Modus {old} → {mode}")
        self._changed()
        self.evaluate()
        await self.apply("modus")

    async def approve(self) -> bool:
        now = dt_util.now()
        items = c.offered(self.coordinator.latest_blocks(), _ms(now))
        if not items:
            return False
        approval = c.approve(self.coordinator.latest_blocks(), now, self.approval)
        self.state["approval"] = approval.as_dict()
        until = dt_util.as_local(dt_util.utc_from_timestamp(approval.until_ms / 1000))
        self.log("Freigabe", f"Freigegeben bis {until:%d.%m. %H:%M}: {_describe(items)}")
        self._changed()
        await self._clear_request()
        self.evaluate()
        return True

    async def reject(self) -> bool:
        now = dt_util.now()
        items = c.offered(self.coordinator.latest_blocks(), _ms(now))
        if not items:
            return False
        approval = c.reject(self.coordinator.latest_blocks(), now, self.approval)
        self.state["approval"] = approval.as_dict()
        until = dt_util.as_local(dt_util.utc_from_timestamp(approval.rejected_until_ms / 1000))
        self.log("Abgelehnt", f"Abgelehnt bis {until:%d.%m. %H:%M}")
        self._changed()
        await self._clear_request()
        self.evaluate()
        return True

    async def withdraw(self) -> None:
        self.state["approval"] = c.withdraw(dt_util.now(), self.approval).as_dict()
        self.log("Abgelehnt", "Freigabe zurückgenommen")
        self._changed()
        self.evaluate()

    async def set_number(self, key: str, value: float) -> None:
        """key: wear_ct or grid_charge_kw (settings.WEAR / settings.GRID_CHARGE)."""
        setting = self.settings.get(key)
        if setting.external:
            raise HomeAssistantError(f"Wird von {setting.external} bestimmt (ems: in configuration.yaml)")
        try:
            self.settings.set(key, value)
        except NotChangeable as err:
            raise HomeAssistantError(
                "In configuration.yaml festgelegt (ems: verschleiss_ct / netzladeleistung_kw)"
            ) from err
        self._changed()
        await self.coordinator.async_request_refresh()

    # ---- switching ----

    async def apply(self, trigger: str) -> None:
        """Bring the inverter to the target (former automation 'EMS: Akku steuern')."""
        async with self._lock:
            await self._apply(trigger)

    def yaml_executor_running(self) -> bool:
        """The former YAML executor is still loaded: then this one stays passive (never two controllers)."""
        state = self.hass.states.get(LEGACY_AUTOMATION)
        # an entry left in the registry after the YAML was removed is only a placeholder ("restored")
        running = state is not None and not state.attributes.get("restored")
        if running and not self._warned_yaml:
            self._warned_yaml = True
            _LOGGER.warning("EMS: %s still exists, the integration does not switch", LEGACY_AUTOMATION)
            # not "Fehler": that kind marks a failed own switch and lets the EMS correct it
            self.log("Hinweis", "Alte YAML-Steuerung ist noch aktiv: die Integration schaltet nicht.")
            self._changed()
        return running

    async def _apply(self, trigger: str) -> None:
        if self.yaml_executor_running():
            return
        reading = self.inputs.inverter()
        situation = sw.Situation(
            mode=self.mode,
            target=self.target,
            current=reading.state,
            ready=reading.ready,
            set_by_ems=self.state["set_by_ems"],
            trigger=trigger,
            now=dt_util.now(),
            switch_failed=bool(self.state.get("switch_failed")),
            foreign_reported=sw.ForeignReport.from_dict(self.state.get("foreign_reported")),
            inverter_changed_at=reading.changed_at,
        )
        decision = sw.decide(situation)
        if decision.kind is sw.Kind.NOTHING:
            return
        current, goal = decision.current, decision.goal
        if decision.kind is sw.Kind.REPORT_FOREIGN:
            await self._report_foreign(current, goal, situation.now)
            return
        await self._command(goal)
        if await self._confirmed(goal):
            self.state["switch_failed"] = False
            await self._switched(current, goal, self.mode in (c.WITH_APPROVAL, c.AUTOMATIC))
        else:
            self.state["switch_failed"] = True
            now_state = self.inverter()
            self.log(
                "Fehler",
                f"Umschalten von {current} auf {goal} nicht bestätigt: nach {CONFIRM_S} s meldet der "
                f"Wechselrichter {now_state}",
            )
            self._changed()
            await self.notify(
                "⚠️ EMS Akku",
                f"Konnte nicht auf „{goal}“ schalten, Wechselrichter meldet „{now_state}“. "
                "Ich versuche es in 5 min erneut.",
                tag="ems-akku-fehler",
            )

    async def _report_foreign(self, current: str, goal: str, now: datetime) -> None:
        """Something else is set at the inverter: do not override, report it (decide throttles)."""
        self.state["foreign_reported"] = sw.ForeignReport(current, now).as_dict()
        self.log(
            "Manuell",
            f"Wechselrichter steht auf „{current}“ (nicht vom EMS). "
            f"EMS greift nicht ein, Soll wäre „{goal}“.",
        )
        self._changed()
        await self.notify(
            "🔋 EMS hält sich raus",
            f"Am Wechselrichter ist „{current}“ eingestellt, nicht vom EMS. "
            "Ich schalte nichts, bis wieder Eigenverbrauch läuft.",
            tag="ems-akku-manuell",
        )

    async def _select(self, key: str, option: str) -> None:
        data = {"entity_id": self.conf[key], "option": option}
        await self.hass.services.async_call("select", "select_option", data, blocking=True)

    async def _set(self, entity_id: str, value: float) -> None:
        data = {"entity_id": entity_id, "value": value}
        await self.hass.services.async_call("number", "set_value", data, blocking=True)

    async def _command(self, goal: str) -> None:
        steps = sw.commands_for(
            goal,
            grid_kw=self.settings.grid_charge().value,
            power_configured=bool(self.conf.get(CONF_FORCED_POWER)),
            normal_discharge_w=self.inputs.discharge_upper_w(),
        )
        for step in steps:
            if step.kind == "select":
                await self._select(step.key, step.value)
            else:
                await self._set(self.conf[step.key], step.value)

    async def _confirmed(self, goal: str) -> bool:
        """Wait until the inverter reports the goal (at most CONFIRM_S)."""
        if self.inverter() == goal:
            return True
        done: asyncio.Future[bool] = self.hass.loop.create_future()

        @callback
        def changed(_event: Event) -> None:
            if self.inverter() == goal and not done.done():
                done.set_result(True)

        @callback
        def timeout(_now: datetime) -> None:
            if not done.done():
                done.set_result(False)

        unsub = async_track_state_change_event(self.hass, self.inputs.inverter_entities(), changed)
        cancel = async_call_later(self.hass, CONFIRM_S, timeout)
        try:
            return await done
        finally:
            unsub()
            cancel()

    async def _switched(self, current: str, goal: str, active: bool) -> None:
        now = dt_util.now()
        self.state["set_by_ems"] = goal
        counters = self.inputs.counters()
        record = self.state.get("running_block")
        if record and record.get("action") in ("h", "l"):
            kwh, eur = c.book(record, counters)
            self._book(record, kwh, eur, now)
            self.state["running_block"] = None
        block = None
        if goal in (c.HOLD, c.CHARGE):
            code = c.CODE[goal]
            block = next(
                (
                    b
                    for b in self.coordinator.latest_blocks()
                    if b.code == code and b.start_ms <= _ms(now) < b.end_ms
                ),
                None,
            )
            self.state["running_block"] = c.start_record(
                code, now.isoformat(timespec="minutes"), counters, block
            )
        section = ""
        if block and goal != c.NORMAL:
            von = dt_util.as_local(dt_util.utc_from_timestamp(block.start_ms / 1000))
            bis = dt_util.as_local(dt_util.utc_from_timestamp(block.end_ms / 1000))
            section = f"{von:%H:%M}–{bis:%H:%M}, Ø {block.ct} ct"
        text = f"{current} → {goal}"
        if section:
            text += f" (Plan {section})"
        if not active:
            text += ", Modus „Nur planen“"
        self.log("Geschaltet", text)
        self._changed()
        if self.hass.services.has_service("logbook", "log"):
            data = {"name": "EMS Akku", "message": f"{current} → {goal} ({self.mode})"}
            if self.target_entity_id:
                data["entity_id"] = self.target_entity_id
            await self.hass.services.async_call("logbook", "log", data, blocking=False)
        if self.mode == c.AUTOMATIC and goal != c.NORMAL:
            await self.notify("🔋 EMS", f"{goal} {section}".strip(), tag="ems-akku-info")

    def _book(self, record: dict[str, Any], kwh: float, eur: float, now: datetime) -> None:
        savings = self.state["savings"]
        savings["value"] = round(float(savings["value"]) + eur, 3)
        entry = {
            "start": record.get("start"), "ende": now.isoformat(timespec="minutes"), "was": record["action"],
            "kwh": round(kwh, 2), "betrag": round(eur, 3),
        }  # fmt: skip
        savings["items"] = [entry, *savings["items"]][:SAVINGS_MAX]
        self._archive(archive.SAVINGS, entry)
        what = "Laden aus dem Netz" if record["action"] == "l" else "Entladung gesperrt"
        start = dt_util.parse_datetime(record.get("start") or "")
        since = f" seit {dt_util.as_local(start):%H:%M}" if start else ""
        amount = f"{kwh:.2f}".replace(".", ",")
        done = "geladen" if record["action"] == "l" else "aus dem Netz"
        self.log("Abgerechnet", f"{what}{since}: {amount} kWh {done}, spart geschätzt {_eur(eur)}")

    # ---- approval requests ----

    async def _maybe_request(self, now: datetime) -> None:
        if self.yaml_executor_running() or not self.is_pending:
            return
        asked = dt_util.parse_datetime(self.state.get("requested_at") or "")
        if c.should_request(now, self.pending_since, asked):
            await self.request()

    async def request(self) -> None:
        items = self.offered()
        if not items:
            return
        now = dt_util.now()
        lines = []
        for b in items[:3]:
            von = dt_util.as_local(dt_util.utc_from_timestamp(b.start_ms / 1000))
            bis = dt_util.as_local(dt_util.utc_from_timestamp(b.end_ms / 1000))
            day = "heute" if von.date() == now.date() else "morgen"
            what = "🔒 Entladung gesperrt" if b.code == "h" else "🔌 Laden aus dem Netz"
            kwh = f" {str(b.kwh).replace('.', ',')} kWh" if b.code == "l" else ""
            saves = _eur(b.saves_eur or 0)
            lines.append(f"{day} {von:%H:%M}–{bis:%H:%M} {what}{kwh} zu {round(b.ct)} ct · spart {saves}")
            lines.append(b.reason)
        if len(items) > 3:
            lines.append(f"+ {len(items) - 3} weitere in der EMS-App")
        self.state["requested_at"] = now.isoformat()
        count = "1 Aktion" if len(items) == 1 else f"{len(items)} Aktionen"
        self.log("Anfrage", f"Freigabe angefragt für {count}")
        self._changed()
        await self.notify(
            "🔋 Akku-Plan freigeben?",
            "\n".join(lines),
            tag=NOTIFY_TAG,
            actions=[
                {"action": ACTION_YES, "title": "Ausführen"},
                {"action": ACTION_NO, "title": "Ablehnen", "destructive": True},
            ],
        )

    async def _clear_request(self) -> None:
        await self.notify(None, "clear_notification", tag=NOTIFY_TAG)

    @callback
    def _notification_action(self, event: Event) -> None:
        action = event.data.get("action")
        if action == ACTION_YES:
            self.hass.async_create_task(self.approve())
        elif action == ACTION_NO:
            self.hass.async_create_task(self.reject())

    async def notify(self, title: str | None, message: str, *, tag: str, actions: list | None = None) -> None:
        target = self.conf.get(CONF_NOTIFY)
        if not target or "." not in target:
            return
        domain, service = target.split(".", 1)
        if not self.hass.services.has_service(domain, service):
            _LOGGER.warning("EMS: notify service %s not found", target)
            return
        data: dict[str, Any] = {"tag": tag}
        if message != "clear_notification":
            data.update({"url": APP_URL, "clickAction": APP_URL})
        if actions:
            data["actions"] = actions
        payload: dict[str, Any] = {"message": message, "data": data}
        if title:
            payload["title"] = title
        try:
            await self.hass.services.async_call(domain, service, payload, blocking=False)
        except Exception as err:
            _LOGGER.warning("EMS: push failed: %s", err)

    # ---- log, forecast, load profile ----

    def log(self, kind: str, text: str) -> None:
        soc = self.inputs.soc()
        entry = {
            "zeit": dt_util.now().isoformat(timespec="seconds"),
            "art": kind,
            "text": text,
            "modus": self.mode,
            "soc": round(soc) if soc is not None else None,
            "preis": self.price_ct(),
        }
        self.state["log"] = [entry, *self.state["log"]][:LOG_MAX]
        self._archive(archive.LOG, entry)

    @callback
    def _forecast(self, _now: datetime) -> None:
        """Daily at 14:05 (tomorrow's prices are there): add the plan's expected savings of the next 24 h."""
        data = self.coordinator.data
        value = as_number(data.attribute.get("ersparnis_24h")) if data else None
        if value is None:
            return
        forecast = self.state["forecast"]
        forecast["value"] = round(float(forecast["value"]) + value, 3)
        forecast["last_day"] = value
        entry = {"at": dt_util.now().isoformat(timespec="minutes"), "value": value}
        forecast["days"] = [*forecast.get("days", []), entry][-FORECAST_DAYS:]
        self._archive(archive.FORECAST, entry)
        self._changed()

    async def reset_forecast(self) -> None:
        """Start the forecast sum anew (e.g. after a calculation error); measured savings stay."""
        old = float(self.state["forecast"]["value"])
        today = dt_util.now().date().isoformat()
        self.state["forecast"] = {"value": 0.0, "last_day": None, "since": today, "days": []}
        self.log("Hinweis", f"Prognose zurückgesetzt (vorher {_eur(old)}), Vergleich beginnt neu")
        self._changed()

    def _remember_meter(self) -> None:
        profile = self.state["load_profile"]
        profile["meter"] = self.inputs.consumed_kwh()
        profile["at"] = dt_util.now().isoformat()
        self._save()

    @callback
    def _learn(self, now: datetime) -> None:
        profile = self.state["load_profile"]
        before = dt_util.parse_datetime(profile.get("at") or "")
        meter = self.inputs.consumed_kwh()
        before_local = dt_util.as_local(before) if before else None
        profile["values"] = c.learn_profile(profile["values"], profile.get("meter"), before_local, meter, now)
        profile["meter"] = meter
        profile["at"] = now.isoformat()
        self._changed()
        self.hass.async_create_task(self.coordinator.async_request_refresh())


def _describe(items: list[PlanBlock]) -> str:
    parts = []
    for b in items:
        von = dt_util.as_local(dt_util.utc_from_timestamp(b.start_ms / 1000))
        bis = dt_util.as_local(dt_util.utc_from_timestamp(b.end_ms / 1000))
        kwh = str(b.kwh).replace(".", ",")
        what = "Entladung gesperrt" if b.code == "h" else f"Laden aus dem Netz, {kwh} kWh"
        saves = _eur(b.saves_eur or 0)
        parts.append(f"{von:%d.%m. %H:%M}–{bis:%H:%M} {what} zu {round(b.ct)} ct (spart {saves})")
    return "; ".join(parts)


def _read_takeover(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        _LOGGER.warning("EMS: takeover file %s unreadable: %s", path, err)
        return None
    path.rename(path.with_suffix(".json.uebernommen"))
    return data if isinstance(data, dict) else None
