// The EMS app: sidebar panel of the expensive-mini-savings integration.

import { LitElement, css, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define } from "./chart";
import { cardStyles } from "./theme";
import type { AppConfig, HomeAssistant } from "./types";
import "./views/plan-view";
import "./views/savings-view";
import "./views/history-view";
import "./views/log-view";
import "./views/settings-view";

const TABS = [
  { id: "plan", name: "Plan", icon: "mdi:calendar-clock" },
  { id: "ersparnis", name: "Ersparnis", icon: "mdi:piggy-bank" },
  { id: "verlauf", name: "Verlauf", icon: "mdi:history" },
  { id: "protokoll", name: "Protokoll", icon: "mdi:clipboard-text-clock" },
  { id: "einstellungen", name: "Einstellungen", icon: "mdi:tune" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export class EmsPanel extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;
  @property({ type: Boolean }) narrow = false;
  @property({ attribute: false }) route?: { path: string; prefix: string };
  @state() private config?: AppConfig;
  @state() private error?: string;
  @state() private tab: Tab = "plan";

  static styles = [
    cardStyles,
    css`
      :host {
        display: block;
        min-height: 100vh;
        background: var(--primary-background-color);
        color: var(--primary-text-color);
      }
      header {
        position: sticky;
        top: 0;
        z-index: 2;
        background: var(--app-header-background-color, var(--primary-background-color));
        color: var(--app-header-text-color, var(--primary-text-color));
        border-bottom: var(--app-header-border-bottom, 1px solid var(--divider-color));
      }
      .bar {
        display: flex;
        align-items: center;
        height: var(--header-height, 56px);
        padding: 0 12px;
        gap: 4px;
      }
      .title {
        font-size: 1.25rem;
        margin-left: 4px;
      }
      nav {
        display: flex;
        overflow-x: auto;
        padding: 0 8px;
        scrollbar-width: none;
      }
      nav button {
        font: inherit;
        font-size: 0.95rem;
        background: none;
        border: none;
        color: var(--secondary-text-color);
        padding: 10px 14px;
        border-bottom: 2px solid transparent;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        white-space: nowrap;
      }
      nav button ha-icon {
        --mdc-icon-size: 18px;
      }
      nav button[aria-selected="true"] {
        color: var(--primary-color);
        border-bottom-color: var(--primary-color);
      }
      main {
        padding: 16px;
        max-width: 1400px;
        margin: 0 auto;
        box-sizing: border-box;
      }
      @media (max-width: 600px) {
        main {
          padding: 12px 8px;
        }
        nav button span {
          display: none;
        }
        nav button[aria-selected="true"] span {
          display: inline;
        }
      }
    `,
  ];

  connectedCallback(): void {
    super.connectedCallback();
    const fromHash = location.hash.slice(1) as Tab;
    if (TABS.some((t) => t.id === fromHash)) this.tab = fromHash;
  }

  protected updated(): void {
    if (this.hass && !this.config && !this.error) this.loadConfig();
  }

  private async loadConfig(): Promise<void> {
    try {
      this.config = await this.hass.callWS<AppConfig>({ type: "ems/config" });
    } catch (e) {
      this.error = (e as { message?: string }).message ?? String(e);
    }
  }

  private select(tab: Tab): void {
    this.tab = tab;
    history.replaceState(null, "", `#${tab}`);
  }

  render() {
    return html`<header>
        <div class="bar">
          <ha-menu-button .hass=${this.hass} .narrow=${this.narrow}></ha-menu-button>
          <span class="title">Energie-Management</span>
        </div>
        <nav role="tablist">
          ${TABS.map(
            (t) =>
              html`<button role="tab" aria-selected=${this.tab === t.id} @click=${() => this.select(t.id)}>
                <ha-icon icon=${t.icon}></ha-icon><span>${t.name}</span>
              </button>`,
          )}
        </nav>
      </header>
      <main>${this.renderTab()}</main>`;
  }

  private renderTab() {
    if (this.error) return html`<div class="card warn error">EMS nicht erreichbar: ${this.error}</div>`;
    if (!this.config || !this.hass) return html`<div class="muted">Lädt …</div>`;
    const c = this.config;
    switch (this.tab) {
      case "plan":
        return html`<ems-plan-view .hass=${this.hass} .config=${c} .narrow=${this.narrow}></ems-plan-view>`;
      case "ersparnis":
        return html`<ems-savings-view .hass=${this.hass} .config=${c}></ems-savings-view>`;
      case "verlauf":
        return html`<ems-history-view .hass=${this.hass} .config=${c}></ems-history-view>`;
      case "protokoll":
        return html`<ems-log-view .hass=${this.hass} .config=${c}></ems-log-view>`;
      case "einstellungen":
        return html`<ems-settings-view .hass=${this.hass} .config=${c}></ems-settings-view>`;
    }
    return nothing;
  }
}
define("ems-panel", EmsPanel);
