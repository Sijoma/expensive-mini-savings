// Minimal types for the parts of the Home Assistant frontend the app uses.

export interface HassEntity {
  entity_id: string;
  state: string;
  attributes: Record<string, any>;
  last_changed: string;
  last_updated: string;
}

export interface HomeAssistant {
  states: Record<string, HassEntity>;
  callWS<T>(msg: Record<string, unknown>): Promise<T>;
  callService(domain: string, service: string, data?: Record<string, unknown>): Promise<unknown>;
  themes?: { darkMode?: boolean };
  user?: { id: string; is_admin: boolean };
}

/** Answer of the websocket command ems/config. Keys are German, as in configuration.yaml. */
export interface AppConfig {
  version: string;
  sensoren: { plan: string | null; wert: string | null; waermepumpe: string | null };
  entitaeten: Record<string, string>;
  einstellungen: Record<string, unknown>;
  yaml: boolean;
  eintrag: string | null;
}

/** One row of the `slots` attribute, see adapter.plan_attributes. */
export type SlotRow = [
  number, // 0 start ms
  number, // 1 price ct
  number, // 2 PV kW (AC)
  number, // 3 load kW
  number, // 4 SoC % at the end
  string, // 5 battery action n/h/l
  string, // 6 heat pump n/pv
  number, // 7 grid charge kWh
  number, // 8 value of 1 kWh in the battery, ct
  number, // 9 SoC % without control
  number, // 10 PV → house
  number, // 11 battery → house
  number, // 12 grid → house
  number, // 13 PV → battery
  number, // 14 PV → grid
  number, // 15 grid → battery
];

export interface PlanBlock {
  von: number;
  bis: number;
  was: "h" | "l";
  kwh: number;
  ct: number;
  spart: number;
  netz_kwh?: number;
  grund?: string;
}

export interface HistoryRow {
  start: number;
  preis_ct: number | null;
  soc: number | null;
  aktion: string | null;
  plan_aktion: string | null;
  plan_soc: number | null;
  pv_kw: number | null;
  last_kw: number | null;
  bezug_kwh: number | null;
  einspeisung_kwh: number | null;
}

export interface HistoryDay {
  tag: string;
  viertelstunden: HistoryRow[];
  plaene: number;
  tage: string[];
}
