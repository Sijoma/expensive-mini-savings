import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define, type ChartOption } from "../chart";
import { num } from "../format";
import { available, configured } from "../ha";
import { profileOption } from "../options";
import { cardStyles } from "../theme";
import type { AppConfig, HassEntity, HomeAssistant } from "../types";

/** Wait this long after the last tap before the value is sent (each change makes the planner recompute). */
const SEND_AFTER_MS = 800;

const pct = (v: number): string => `${num(v * 100, v * 100 === Math.round(v * 100) ? 0 : 1)} %`;

export class EmsSettingsView extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;
  @property({ attribute: false }) config!: AppConfig;
  /** Values tapped but not yet confirmed by Home Assistant, per entity. */
  @state() private pending: Record<string, number> = {};
  @state() private error?: string;

  private timers: Record<string, number> = {};
  private profileFor?: unknown;
  private profileChart?: ChartOption;

  static styles = [cardStyles];

  disconnectedCallback(): void {
    super.disconnectedCallback();
    Object.values(this.timers).forEach((t) => window.clearTimeout(t));
  }

  private change(entity: HassEntity, value: number) {
    const id = entity.entity_id;
    this.pending = { ...this.pending, [id]: value };
    this.error = undefined;
    window.clearTimeout(this.timers[id]);
    this.timers[id] = window.setTimeout(() => this.send(id, value), SEND_AFTER_MS);
  }

  private async send(id: string, value: number) {
    try {
      await this.hass.callService(id.split(".")[0], "set_value", { entity_id: id, value });
    } catch (e) {
      const message = (e as { message?: string }).message ?? String(e);
      this.error = `Der Wert konnte nicht gespeichert werden (${message}). Bitte noch einmal versuchen.`;
    } finally {
      const rest = { ...this.pending };
      delete rest[id];
      this.pending = rest;
    }
  }

  private number(key: string, label: string, unit: string, help: string) {
    const e = configured(this.hass, this.config, key);
    if (!e) {
      return html`<div class="setting">
        <div>
          <b>${label}</b>
          <div class="muted small">Keine Entität gesetzt. Der Planer rechnet mit seinem Standardwert.</div>
        </div>
      </div>`;
    }
    const waiting = e.entity_id in this.pending;
    const v = waiting ? this.pending[e.entity_id] : Number(e.state);
    const step = Number(e.attributes.step ?? 1);
    const min = Number(e.attributes.min ?? 0);
    const max = Number(e.attributes.max ?? 100);
    const editable = ["input_number", "number"].includes(e.entity_id.split(".")[0]);
    const digits = step < 1 ? 1 : 0;
    const shown = available(e) || waiting ? `${num(v, digits)} ${unit}` : "–";
    return html`<div class="setting">
      <div>
        <b id="set-${key}">${label}</b>
        <div class="muted small">${help}${waiting ? " · wird übernommen …" : ""}</div>
      </div>
      <div class="stepper" role="group" aria-labelledby="set-${key}">
        ${editable
          ? html`<button
              aria-label="${label} verringern"
              ?disabled=${v - step < min - 1e-9}
              @click=${() => this.change(e, Math.max(min, +(v - step).toFixed(3)))}
            >
              <ha-icon icon="mdi:minus"></ha-icon>
            </button>`
          : nothing}
        <span class="val" aria-live="polite">${shown}</span>
        ${editable
          ? html`<button
              aria-label="${label} erhöhen"
              ?disabled=${v + step > max + 1e-9}
              @click=${() => this.change(e, Math.min(max, +(v + step).toFixed(3)))}
            >
              <ha-icon icon="mdi:plus"></ha-icon>
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
    const p = this.config.planer;
    const feedIn = Number(this.config.einstellungen.einspeisung_ct ?? 7);
    return html`<style>
        .setting {
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 12px;
          padding: 12px 0;
          border-bottom: 1px solid var(--divider-color);
        }
        .stepper {
          display: flex;
          align-items: center;
          gap: 8px;
          flex: none;
        }
        .stepper button {
          width: 44px;
          height: 44px;
          border-radius: 50%;
          border: 1px solid var(--divider-color);
          background: transparent;
          color: var(--primary-text-color);
          cursor: pointer;
          display: grid;
          place-items: center;
        }
        .stepper button:disabled {
          opacity: 0.4;
          cursor: default;
        }
        .stepper .val {
          min-width: 72px;
          text-align: center;
          font-variant-numeric: tabular-nums;
          font-weight: 600;
        }
        .facts {
          margin: 0;
          padding-left: 1.2em;
          line-height: 1.5;
        }
        code {
          font-size: 0.85em;
        }
      </style>
      <div class="grid">
        <section class="card" aria-labelledby="set-title">
          <h2 id="set-title"><ha-icon icon="mdi:tune"></ha-icon>Einstellungen</h2>
          ${this.number("verschleiss", "Akku-Verschleiß", "ct", "Was es kostet, 1 kWh aus dem Akku zu nehmen")}
          ${this.number(
            "netzladeleistung",
            "Netz-Ladeleistung",
            "kW",
            `Wie schnell der Akku aus dem Netz lädt${p ? ` (höchstens ${num(p.netzladen_max_kw, 0)} kW)` : ""}`,
          )}
          ${this.error ? html`<div class="warn error">${this.error}</div>` : nothing}
          <p class="muted small">
            Der Plan rechnet nach jeder Änderung neu. Ist für die Entität in YAML ein <code>initial:</code> gesetzt,
            gilt die Änderung nur bis zum nächsten Neustart von Home Assistant.
          </p>
        </section>
        <section class="card" aria-labelledby="profile-title">
          <h2 id="profile-title"><ha-icon icon="mdi:home-lightning-bolt-outline"></ha-icon>Lastprofil</h2>
          ${this.profileChart
            ? html`<ems-chart
                  .option=${this.profileChart}
                  .height=${180}
                  .label=${`Erwarteter Hausverbrauch je Stunde, im Mittel ${profile?.state ?? "–"} Watt.`}
                ></ems-chart>
                <p class="muted small">
                  So viel verbraucht das Haus üblicherweise je Stunde${available(profile)
                    ? ` (im Mittel ${profile.state} W)`
                    : ""}. Die aktuelle Stunde ist hervorgehoben.
                </p>`
            : html`<p class="muted">Kein Lastprofil gesetzt. Der Planer rechnet mit 500 W rund um die Uhr.</p>`}
        </section>
        <section class="card" aria-labelledby="calc-title">
          <h2 id="calc-title"><ha-icon icon="mdi:calculator-variant-outline"></ha-icon>So rechnet der Planer</h2>
          ${p
            ? html`<ul class="facts small">
                <li>
                  Verluste: PV → Akku ${pct(1 - p.pv_akku)}, Netz → Akku ${pct(1 - p.netz_akku)}, Akku → Haus
                  ${pct(1 - p.akku_haus)}, dazu ${p.standby_w} W Eigenverbrauch des Wechselrichters beim Entladen ohne
                  PV.
                </li>
                <li>Aus dem Netz laden nur, wenn es nach Verlusten und Verschleiß günstiger ist als später.</li>
                <li>
                  Jede neue Aktion kostet rechnerisch ${num(p.wechsel_ct, 0)} ct (kein Hin und Her), gesperrt wird
                  mindestens ${p.sperren_min} Minuten.
                </li>
                <li>
                  Verbrauch = Lastprofil × ${num(p.last_faktor, 1)}. PV = ${pct(1 - p.pv_p10_anteil)} Prognose +
                  ${pct(p.pv_p10_anteil)} „schlechter Tag“, dazu kurze Spitzen innerhalb der Viertelstunde.
                </li>
                <li>Untergrenze ist die Notstrom-Reserve. Einspeisung bringt ${num(feedIn, 1)} ct je kWh.</li>
              </ul>`
            : html`<p class="muted">Die Rechenwerte sind erst mit einer neueren Version der Integration sichtbar.</p>`}
        </section>
        <section class="card" aria-labelledby="conf-title">
          <h2 id="conf-title"><ha-icon icon="mdi:cog-outline"></ha-icon>Konfiguration</h2>
          ${this.config.yaml
            ? html`<p class="small" style="margin-top:0">
                Die Eingaben stehen in <code>configuration.yaml</code> unter <code>ems:</code>. Zum Ändern dort
                bearbeiten und Home Assistant neu starten.
              </p>`
            : html`<p class="small" style="margin-top:0">
                Ändern unter
                <a href="/config/integrations/integration/ems"
                  >Einstellungen → Geräte & Dienste → Expensive Mini Savings → Konfigurieren</a
                >.
              </p>`}
          <details>
            <summary>Alle Eingaben anzeigen</summary>
            <div class="scroll">
              <table class="small">
                ${Object.entries(this.config.einstellungen).map(
                  ([k, v]) =>
                    html`<tr>
                      <td><code>${k}</code></td>
                      <td>${v === true ? "ja" : v === false ? "nein" : String(v)}</td>
                    </tr>`,
                )}
                ${Object.entries(this.config.entitaeten).map(
                  ([k, v]) =>
                    html`<tr>
                      <td><code>${k}</code></td>
                      <td>${v}</td>
                    </tr>`,
                )}
              </table>
            </div>
          </details>
        </section>
      </div>`;
  }
}
define("ems-settings-view", EmsSettingsView);
