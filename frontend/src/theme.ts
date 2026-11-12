import { css } from "lit";

/** Battery action codes: plan (n/h/l) and measured (also f/e/v). Names follow the fixed terms in
 * PRODUCT.md; `sentence` says what the battery does, for headlines. */
export const ACTIONS: Record<
  string,
  { name: string; sentence: string; icon: string; color: string }
> = {
  n: { name: "Eigenverbrauch", sentence: "Der Akku läuft normal", icon: "mdi:battery-sync", color: "#8d9499" },
  h: {
    name: "Entladung gesperrt",
    sentence: "Der Akku hält seine Ladung",
    icon: "mdi:battery-lock",
    color: "#488fc2",
  },
  l: {
    name: "Laden aus dem Netz",
    sentence: "Der Akku lädt aus dem Netz",
    icon: "mdi:transmission-tower-import",
    color: "#e0457b",
  },
  f: { name: "Angehalten", sentence: "Der Akku ist angehalten", icon: "mdi:battery-lock", color: "#6aa6ef" },
  e: {
    name: "Erzwungenes Entladen",
    sentence: "Der Akku wird entladen",
    icon: "mdi:battery-arrow-down",
    color: "#8e44ad",
  },
  v: { name: "Anbieter steuert", sentence: "Ein Anbieter steuert den Akku", icon: "mdi:cloud-sync", color: "#b33b2e" },
};

export const NAME_TO_CODE: Record<string, string> = Object.fromEntries(
  Object.entries(ACTIONS).map(([code, a]) => [a.name, code]),
);

/** Colors follow the Home Assistant energy dashboard (solar orange, battery teal, grid blue, battery
 * charging pink, exported solar purple); prices use the usual green → amber → red scale. */
export const COLORS = {
  value: "#00897b",
  soc: "#26a69a",
  socWithout: "#8d9499",
  pv: "#ff9800",
  load: "#607d8b",
  boost: "#8353d1",
  measured: "#0f9d58",
  forecast: "#a8adb3",
  cheap: "rgb(46,157,74)",
  middle: "rgb(242,183,5)",
  expensive: "rgb(217,68,47)",
};

/** Shared look, following the Home Assistant theme variables. */
export const cardStyles = css`
  :host {
    display: block;
    font-size: 1rem;
    line-height: 1.45;
    --ems-accent: color-mix(in srgb, var(--primary-color, #03a9f4) 70%, black);
  }
  .card {
    background: var(--ha-card-background, var(--card-background-color, #fff));
    border-radius: var(--ha-card-border-radius, 12px);
    border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color, #e0e0e0));
    box-shadow: var(--ha-card-box-shadow, none);
    padding: 20px;
    box-sizing: border-box;
    min-width: 0;
  }
  h2 {
    margin: 0 0 14px;
    font-size: 1.3rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
    letter-spacing: -0.005em;
  }
  h2 ha-icon {
    --mdc-icon-size: 24px;
    color: var(--secondary-text-color);
  }
  .muted {
    color: var(--secondary-text-color);
  }
  .small {
    font-size: 0.9rem;
  }
  .num-tab {
    font-variant-numeric: tabular-nums;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
  }
  .grid {
    display: grid;
    gap: 16px;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 420px), 1fr));
  }
  .wide {
    grid-column: 1 / -1;
  }
  /* Notice: tinted surface with an icon, no side stripe */
  .warn {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 10px 12px;
    background: color-mix(in srgb, var(--warning-color, #ffa600) 14%, transparent);
    border-radius: 8px;
    margin: 8px 0;
    font-size: 0.9rem;
    line-height: 1.45;
  }
  .warn::before {
    content: "";
    flex: none;
    width: 8px;
    height: 8px;
    margin-top: 0.45em;
    border-radius: 50%;
    background: var(--warning-color, #ffa600);
  }
  .warn.error {
    background: color-mix(in srgb, var(--error-color, #db4437) 14%, transparent);
  }
  .warn.error::before {
    background: var(--error-color, #db4437);
  }
  .warn.ok {
    background: color-mix(in srgb, var(--success-color, #43a047) 14%, transparent);
  }
  .warn.ok::before {
    background: var(--success-color, #43a047);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.95rem;
    font-variant-numeric: tabular-nums;
  }
  th,
  td {
    text-align: left;
    padding: 8px 6px;
    border-bottom: 1px solid var(--divider-color, #e0e0e0);
    vertical-align: top;
  }
  th {
    font-weight: 500;
    color: var(--secondary-text-color);
  }
  td.num,
  th.num {
    text-align: right;
    white-space: nowrap;
  }
  .scroll {
    overflow-x: auto;
  }
  /* Action label: tinted surface, colored dot, text in the normal text color (contrast in both themes) */
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 2px 10px 2px 8px;
    border-radius: 999px;
    font-size: 0.9rem;
    white-space: nowrap;
    color: var(--primary-text-color);
    background: color-mix(in srgb, var(--pill, #9a9a94) 18%, transparent);
  }
  .pill::before {
    content: "";
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--pill, #9a9a94);
  }
  button.action {
    font: inherit;
    font-weight: 500;
    border: none;
    border-radius: 999px;
    min-height: 44px;
    padding: 0 20px;
    cursor: pointer;
    background: var(--ems-accent);
    color: #fff;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  button.action ha-icon {
    --mdc-icon-size: 20px;
  }
  button.action.secondary {
    background: transparent;
    color: var(--primary-text-color);
    border: 1px solid var(--divider-color);
  }
  button.action:disabled {
    opacity: 0.5;
    cursor: default;
  }
  button:focus-visible,
  [role="tab"]:focus-visible,
  [role="radio"]:focus-visible,
  summary:focus-visible,
  a:focus-visible {
    outline: 2px solid var(--primary-color, #03a9f4);
    outline-offset: 2px;
  }
  a {
    color: var(--primary-color);
    text-underline-offset: 2px;
  }
  .kpis {
    display: grid;
    gap: 12px;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  }
  .kpi {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .kpi .value {
    font-size: 1.7rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .kpi .label {
    color: var(--secondary-text-color);
    font-size: 0.95rem;
  }
  details summary {
    cursor: pointer;
    color: var(--secondary-text-color);
    min-height: 44px;
    display: list-item;
  }
  ::selection {
    background: color-mix(in srgb, var(--primary-color, #03a9f4) 30%, transparent);
  }
`;

export function cssVar(el: Element, name: string, fallback: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim() || fallback;
}

/** Pill markup helper value: style attribute for an action color. */
export const pillColor = (code: string): string => `--pill:${(ACTIONS[code] ?? ACTIONS.n).color}`;
