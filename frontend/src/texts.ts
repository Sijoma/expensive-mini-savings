// Plain German for values that arrive from integrations and YAML in raw or English form.

/** Known inverter states (Sungrow Modbus integration) and generic HA states. */
const STATES: Record<string, string> = {
  "Self-consumption mode (default)": "Eigenverbrauch",
  "Forced mode": "Erzwungener Betrieb",
  "External EMS mode": "Externe Steuerung",
  VPP: "Anbieter steuert",
  "Stop (default)": "Stopp",
  "Forced charge": "Zwangsladen",
  "Forced discharge": "Zwangsentladen",
  unavailable: "nicht erreichbar",
  unknown: "unbekannt",
  None: "–",
  on: "an",
  off: "aus",
};

export function plainState(state: string | null | undefined): string {
  if (state == null) return "–";
  return STATES[state] ?? state;
}

const ESCAPED = Object.keys(STATES)
  .filter((k) => k.length > 3) // short words (on/off) only as whole values, never inside sentences
  .sort((a, b) => b.length - a.length)
  .map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
const IN_TEXT = new RegExp(`(${ESCAPED.join("|")})`, "g");

/** Cleans a sentence written elsewhere: English states, `None`, decimal points before units, -0. */
export function plainText(text: string | null | undefined): string {
  if (!text) return "";
  return text
    .replace(IN_TEXT, (m) => STATES[m] ?? m)
    .replace(/\bNone\b/g, "–")
    .replace(/(\d)\.(\d+)(\s?(?:ct|kWh|kW|€|%))/g, "$1,$2$3")
    .replace(/-0(,0+)?(\s?(?:kWh|€))/g, "0$1$2");
}

/** Collapses consecutive equal entries into one with a count. */
export function collapse<T>(items: T[], key: (item: T) => string): { item: T; count: number }[] {
  const out: { item: T; count: number }[] = [];
  for (const item of items) {
    const last = out[out.length - 1];
    if (last && key(last.item) === key(item)) last.count += 1;
    else out.push({ item, count: 1 });
  }
  return out;
}
