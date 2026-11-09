// Formatting for the German UI.

const DAY_MS = 86_400_000;

/** Rounds first, so tiny negative values never show as "-0,0". */
function fixed(v: unknown, digits: number): string {
  const f = 10 ** digits;
  const r = Math.round((Number(v) || 0) * f) / f;
  return (r === 0 ? 0 : r).toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export const eur = (v: unknown): string => `${fixed(v, 2)} €`;

export const num = (v: unknown, digits = 1): string => fixed(v, digits);

export const ct = (v: unknown): string => `${fixed(v, 0)} ct`;

/** "09.11.2026" from "2026-11-09" or an ISO time; unchanged if not a date. */
export function day(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = Date.parse(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return Number.isNaN(t)
    ? iso
    : new Date(t).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export const time = (ms: number): string =>
  new Date(ms).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });

export const dateTime = (ms: number): string =>
  new Date(ms).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });

/** "heute 14:00", "morgen 06:45" or "12.11. 06:45". */
export function when(ms: number, now = Date.now()): string {
  const start = (t: number) => new Date(t).setHours(0, 0, 0, 0);
  const days = Math.round((start(ms) - start(now)) / DAY_MS);
  if (days === 0) return `heute ${time(ms)}`;
  if (days === 1) return `morgen ${time(ms)}`;
  if (days === -1) return `gestern ${time(ms)}`;
  return dateTime(ms);
}

export const toMs = (iso: string | null | undefined): number | null => {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
};
