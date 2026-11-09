// Formatting for the German UI.

const DAY_MS = 86_400_000;

export const eur = (v: unknown): string =>
  `${(Number(v) || 0).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;

export const num = (v: unknown, digits = 1): string =>
  (Number(v) || 0).toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const ct = (v: unknown): string => `${Math.round(Number(v) || 0)} ct`;

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
