import { css } from "lit";

/** Battery action codes: plan (n/h/l) and measured (also f/e/v). */
export const ACTIONS: Record<string, { name: string; short: string; icon: string; color: string }> = {
  n: { name: "Eigenverbrauch", short: "Normal", icon: "mdi:battery-sync", color: "#a8a8a3" },
  h: { name: "Entladung gesperrt", short: "Sperren", icon: "mdi:battery-lock", color: "#3987e5" },
  l: { name: "Laden aus dem Netz", short: "Netzladen", icon: "mdi:transmission-tower-import", color: "#d95926" },
  f: { name: "Stopp (Forced)", short: "Stopp", icon: "mdi:battery-lock", color: "#6aa6ef" },
  e: { name: "Erzwungenes Entladen", short: "Entladen", icon: "mdi:battery-arrow-down", color: "#8e44ad" },
  v: { name: "Anbieter steuert", short: "Anbieter", icon: "mdi:cloud-sync", color: "#c0392b" },
};

export const NAME_TO_CODE: Record<string, string> = Object.fromEntries(
  Object.entries(ACTIONS).map(([code, a]) => [a.name, code]),
);

export const COLORS = {
  value: "#2e9d4a",
  soc: "#7a6ee0",
  socWithout: "#9a9a95",
  pv: "#e0a100",
  load: "#8a8a85",
  boost: "#e8590c",
  measured: "#3987e5",
  forecast: "#a8a8a3",
};

/** Shared card look, following the Home Assistant theme variables. */
export const cardStyles = css`
  :host {
    display: block;
  }
  .card {
    background: var(--ha-card-background, var(--card-background-color, #fff));
    border-radius: var(--ha-card-border-radius, 12px);
    border: var(--ha-card-border-width, 1px) solid var(--ha-card-border-color, var(--divider-color, #e0e0e0));
    box-shadow: var(--ha-card-box-shadow, none);
    padding: 16px;
    box-sizing: border-box;
    min-width: 0;
  }
  h2 {
    margin: 0 0 12px;
    font-size: 1.05rem;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h2 ha-icon {
    --mdc-icon-size: 20px;
    color: var(--secondary-text-color);
  }
  .muted {
    color: var(--secondary-text-color);
  }
  .small {
    font-size: 0.85rem;
  }
  .grid {
    display: grid;
    gap: 16px;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 340px), 1fr));
  }
  .wide {
    grid-column: 1 / -1;
  }
  .warn {
    border-left: 4px solid var(--warning-color, #ffa600);
    padding: 8px 12px;
    background: color-mix(in srgb, var(--warning-color, #ffa600) 12%, transparent);
    border-radius: 6px;
    margin: 8px 0;
  }
  .error {
    border-left-color: var(--error-color, #db4437);
    background: color-mix(in srgb, var(--error-color, #db4437) 12%, transparent);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
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
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 0.8rem;
    white-space: nowrap;
    color: #fff;
  }
  button.action {
    font: inherit;
    border: none;
    border-radius: 999px;
    padding: 8px 16px;
    cursor: pointer;
    background: var(--primary-color);
    color: var(--text-primary-color, #fff);
    display: inline-flex;
    align-items: center;
    gap: 6px;
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
    font-size: 1.5rem;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .kpi .label {
    color: var(--secondary-text-color);
    font-size: 0.85rem;
  }
  details summary {
    cursor: pointer;
    color: var(--secondary-text-color);
  }
`;

export function cssVar(el: Element, name: string, fallback: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim() || fallback;
}
