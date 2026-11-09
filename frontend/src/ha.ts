import type { AppConfig, HassEntity, HomeAssistant } from "./types";

/** State of a configured input or executor entity (key as in configuration.yaml). */
export function configured(hass: HomeAssistant, config: AppConfig, key: string): HassEntity | undefined {
  const id = config.entitaeten[key];
  return id ? hass.states[id] : undefined;
}

export function sensor(
  hass: HomeAssistant,
  config: AppConfig,
  name: keyof AppConfig["sensoren"],
): HassEntity | undefined {
  const id = config.sensoren[name];
  return id ? hass.states[id] : undefined;
}

export const available = (e?: HassEntity): e is HassEntity =>
  !!e && e.state !== "unavailable" && e.state !== "unknown";

/** Statistic change over a calendar period (day/week/month) via the recorder. */
export async function statisticChange(
  hass: HomeAssistant,
  statisticId: string,
  period: "day" | "week" | "month",
): Promise<number | null> {
  try {
    const r = await hass.callWS<{ change?: number | null }>({
      type: "recorder/statistic_during_period",
      statistic_id: statisticId,
      calendar: { period },
      types: ["change"],
    });
    return r.change ?? null;
  } catch {
    return null;
  }
}

/** Change per day for the last `days` days, aligned to the given day list (YYYY-MM-DD). */
export async function dailyChanges(
  hass: HomeAssistant,
  ids: string[],
  days: number,
): Promise<{ days: string[]; values: Record<string, (number | null)[]> }> {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - (days - 1));
  const list: string[] = [];
  for (let i = 0; i < days; i++) {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    list.push(isoDay(d));
  }
  const values: Record<string, (number | null)[]> = {};
  try {
    const r = await hass.callWS<Record<string, { start: number | string; change?: number | null }[]>>({
      type: "recorder/statistics_during_period",
      start_time: start.toISOString(),
      statistic_ids: ids,
      period: "day",
      types: ["change"],
    });
    for (const id of ids) {
      const byDay = new Map((r[id] ?? []).map((p) => [isoDay(new Date(p.start)), p.change ?? null]));
      values[id] = list.map((d) => byDay.get(d) ?? null);
    }
  } catch {
    for (const id of ids) values[id] = list.map(() => null);
  }
  return { days: list, values };
}

export function isoDay(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
