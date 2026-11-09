// Logbook entries of the executor entities: what changed and who caused it.

import { plainState, plainText } from "./texts";
import type { HomeAssistant } from "./types";

export interface LogbookEntry {
  when: number | string; // websocket: seconds since epoch; REST: ISO time
  entity_id?: string;
  name?: string;
  state?: string;
  message?: string;
  domain?: string;
  context_user_id?: string;
  context_event_type?: string;
  context_domain?: string;
  context_service?: string;
  context_name?: string;
  context_entity_id?: string;
  context_entity_id_name?: string;
}

export interface LogbookRow {
  ms: number;
  entity: string;
  name: string;
  what: string;
  by: string;
  /** user | automation | entity | service | device | external */
  kind: string;
}

/** Keys (as in configuration.yaml) whose changes the app shows. */
export const LOGBOOK_KEYS = [
  "soll",
  "modus",
  "ems_modus",
  "erzwungen",
  "entladen_max",
  "freigabe",
  "freigabe_bis",
  "freigeben",
  "ablehnen",
];

const toMs = (when: number | string): number => (typeof when === "number" ? when * 1000 : Date.parse(when));

export function cause(e: LogbookEntry, userId?: string): { by: string; kind: string } {
  if (e.context_user_id) return { by: e.context_user_id === userId ? "du" : "Benutzer", kind: "user" };
  if (e.context_event_type === "automation_triggered")
    return { by: `Automation „${e.context_name ?? e.context_entity_id}“`, kind: "automation" };
  if (e.context_entity_id_name || e.context_entity_id)
    return { by: e.context_entity_id_name ?? e.context_entity_id!, kind: "entity" };
  if (e.context_domain && e.context_service)
    return { by: `${e.context_domain}.${e.context_service}`, kind: "service" };
  return { by: "automatisch berechnet", kind: "device" };
}

const OUTAGE = new Set(["unavailable", "unknown"]);

/** Rows newest first. Without `outages`, connection drops are left out: changes to unavailable/unknown
 * and the return to the state from before. Script runs appear once ("gestartet"), not as on/off. */
/** Inverter keys: a change there without an EMS action shortly before came from outside (VPP, app, by hand). */
export const INVERTER_KEYS = ["ems_modus", "erzwungen", "entladen_max"];
const EXTERNAL_WINDOW_MS = 120_000;

export function logbookRows(
  entries: LogbookEntry[],
  userId?: string,
  outages = false,
  inverter: Set<string> = new Set(),
): LogbookRow[] {
  const chronological = entries
    .filter((e) => e.entity_id)
    .map((e) => ({ e, ms: toMs(e.when) }))
    .filter((x) => !Number.isNaN(x.ms))
    .sort((a, b) => a.ms - b.ms);
  const last = new Map<string, string>();
  const rows: LogbookRow[] = [];
  for (const { e, ms } of chronological) {
    const entity = e.entity_id!;
    const script = entity.startsWith("script.");
    if (script && e.message == null) continue; // on/off of a script run
    if (e.state != null && e.message == null) {
      if (!outages) {
        if (OUTAGE.has(e.state)) continue;
        if (last.get(entity) === e.state) continue;
      }
      last.set(entity, e.state);
    }
    const message = script && e.message === "started" ? "gestartet" : e.message;
    const what = message ? plainText(message) : e.state != null ? `→ ${plainState(e.state)}` : "";
    rows.push({ ms, entity, name: e.name ?? entity, what, ...cause(e, userId) });
  }
  const actions = rows.filter((r) => r.kind === "automation" || r.kind === "user").map((r) => r.ms);
  for (const r of rows) {
    if (!inverter.has(r.entity) || r.kind === "automation" || r.kind === "user") continue;
    if (r.what === `→ ${plainState("unavailable")}` || r.what === `→ ${plainState("unknown")}`) continue;
    const ems = actions.some((t) => t <= r.ms + 5_000 && t >= r.ms - EXTERNAL_WINDOW_MS);
    if (ems) r.by = "Wechselrichter, nach EMS-Befehl";
    else Object.assign(r, { kind: "external", by: "von außen, nicht vom EMS" });
  }
  return rows.reverse();
}

export async function loadLogbook(hass: HomeAssistant, entityIds: string[], hours: number): Promise<LogbookEntry[]> {
  if (!entityIds.length) return [];
  const end = new Date();
  const start = new Date(end.getTime() - hours * 3600_000);
  return hass.callWS<LogbookEntry[]>({
    type: "logbook/get_events",
    start_time: start.toISOString(),
    end_time: end.toISOString(),
    entity_ids: entityIds,
  });
}
