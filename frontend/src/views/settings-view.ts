import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define, type ChartOption } from "../chart";
import { num } from "../format";
import { available, configured } from "../ha";
import { profileOption } from "../options";
import { cardStyles } from "../theme";
import type { AppConfig, HassEntity, HomeAssistant } from "../types";

export class EmsSettingsView extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;
  @property({ attribute: false }) config!: AppConfig;
  @state() private busy = false;

  private profileFor?: unknown;
  private profileChart?: ChartOption;

  static styles = [cardStyles];

  private async set(entity: HassEntity, value: number) {
    const domain = entity.entity_id.split(".")[0];
    this.busy = true;
    try {
      await this.hass.callService(domain, "set_value", { entity_id: entity.entity_id, value });
    } finally {
      this.busy = false;
    }
  }

  private number(key: string, label: string, unit: string, help: string) {
    const e = configured(this.hass, this.config, key);
    if (!e) {
      return html`<div class="setting">
        <div><b>${label}</b><div class="muted small">nicht gesetzt – der Planer nutzt den Standardwert</div></div>
      </div>`;
    }
    const v = Number(e.state);
    const step = Number(e.attributes.step ?? 1);
    const min = Number(e.attributes.min ?? 0);
    const max = Number(e.attributes.max ?? 100);
    const editable = ["input_number", "number"].includes(e.entity_id.split(".")[0]);
    const digits = step < 1 ? 1 : 0;
    return html`<div class="setting">
      <div>
        <b>${label}</b>
        <div class="muted small">${help}</div>
      </div>
      <div class="stepper">
        ${editable
          ? html`<button
              aria-label="weniger"
              ?disabled=${this.busy || v - step < min}
              @click=${() => this.set(e, Math.max(min, +(v - step).toFixed(3)))}
            >
              −
            </button>`
          : nothing}
        <span class="val">${available(e) ? `${num(v, digits)} ${unit}` : "–"}</span>
        ${editable
          ? html`<button
              aria-label="mehr"
              ?disabled=${this.busy || v + step > max}
              @click=${() => this.set(e, Math.min(max, +(v + step).toFixed(3)))}
            >
              +
            </button>`
          : nothing}
      </div>
    </div>`;
  }

  render() {
    const profile = configured(this.hass, this.config, "lastprofil");
    const values: number[] | undefined = profile?.attributes.profil;
    if (values !== this.profileFor) {
      this.profileFor = values;
      this.profileChart = values?.length === 24 ? profileOption(values, new Date().getHours()) : undefined;
    }
    const settings = { ...this.config.einstellungen };
    return html`<style>
        .setting {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          padding: 10px 0;
          border-bottom: 1px solid var(--divider-color);
        }
        .stepper {
          display: flex;
          align-items: center;
          gap: 8px;
          flex: none;
        }
        .stepper button {
          font: inherit;
          font-size: 1.2rem;
          width: 36px;
          height: 36px;
          border-radius: 50%;
          border: 1px solid var(--divider-color);
          background: transparent;
          color: var(--primary-text-color);
          cursor: pointer;
        }
        .stepper .val {
          min-width: 64px;
          text-align: center;
          font-variant-numeric: tabular-nums;
          font-weight: 600;
        }
        code {
          font-size: 0.85em;
        }
      </style>
      <div class="grid">
        <div class="card">
          <h2><ha-icon icon="mdi:tune"></ha-icon>Einstellungen</h2>
          ${this.number("verschleiss", "Akku-Verschleiß", "ct", "Kosten je kWh, die aus dem Akku kommt")}
          ${this.number("netzladeleistung", "Netz-Ladeleistung", "kW", "Leistung beim Laden aus dem Netz (max. 5)")}
          <p class="muted small">
            Änderungen wirken sofort, der Plan rechnet neu. Hat die Entität in YAML ein <code>initial:</code>, gilt
            die Änderung nur bis zum nächsten Neustart von Home Assistant.
          </p>
        </div>
        <div class="card">
          <h2><ha-icon icon="mdi:home-lightning-bolt-outline"></ha-icon>Lastprofil</h2>
          ${this.profileChart
            ? html`<ems-chart .option=${this.profileChart} .height=${180}></ems-chart>
                <p class="muted small">
                  Ø Hausverbrauch je Stunde${available(profile) ? ` (Ø ${profile.state} W)` : ""}. Der Planer nimmt
                  ihn × 1,1.
                </p>`
            : html`<p class="muted">Kein Lastprofil – der Planer rechnet mit 500 W.</p>`}
        </div>
        <div class="card">
          <h2><ha-icon icon="mdi:calculator-variant-outline"></ha-icon>So rechnet der Planer</h2>
          <ul class="small">
            <li>
              Wirkungsgrade: PV → Akku 98 %, Netz → Akku 96 %, Akku → Haus 97,5 %, plus 98 W Standby beim Entladen
              ohne PV.
            </li>
            <li>Netzladen nur, wenn es nach Verlusten und Verschleiß günstiger ist als später.</li>
            <li>Jede neue Aktion kostet rechnerisch 2 ct (kein Hin und Her), Sperren mindestens 30 min.</li>
            <li>
              Verbrauch = Lastprofil × 1,1; PV = 85 % Prognose + 15 % „schlechter Tag“, dazu kurze Spitzen innerhalb
              der Viertelstunde.
            </li>
            <li>Untergrenze = Notstrom-Reserve; Restwert am Ende = günstigster Preis im Plan.</li>
            <li>Wärmepumpe: PV-Boost nur mit Überschuss, der sonst eingespeist würde.</li>
          </ul>
        </div>
        <div class="card">
          <h2><ha-icon icon="mdi:cog-outline"></ha-icon>Konfiguration</h2>
          ${this.config.yaml
            ? html`<div class="warn">
                Kommt aus <code>configuration.yaml</code> (<code>ems:</code>) und hat dort Vorrang. Ändern in der YAML
                und neu starten.
              </div>`
            : html`<p class="small">
                Ändern unter
                <a href="/config/integrations/integration/ems">Einstellungen → Geräte & Dienste → Expensive Mini
                  Savings → Konfigurieren</a>.
              </p>`}
          <details>
            <summary>Alle Einstellungen</summary>
            <table class="small">
              ${Object.entries(settings).map(
                ([k, v]) => html`<tr>
                  <td><code>${k}</code></td>
                  <td>${String(v)}</td>
                </tr>`,
              )}
              ${Object.entries(this.config.entitaeten).map(
                ([k, v]) => html`<tr>
                  <td><code>${k}</code></td>
                  <td>${v}</td>
                </tr>`,
              )}
            </table>
          </details>
        </div>
      </div>`;
  }
}
define("ems-settings-view", EmsSettingsView);
