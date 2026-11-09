import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define } from "../chart";
import { ct, dateTime, time, toMs } from "../format";
import { configured } from "../ha";
import { INVERTER_KEYS, LOGBOOK_KEYS, loadLogbook, logbookRows, type LogbookRow } from "../logbook";
import { cardStyles } from "../theme";
import type { AppConfig, HomeAssistant } from "../types";

const ICONS: Record<string, string> = {
  Geschaltet: "mdi:flash",
  Freigabe: "mdi:check-circle",
  Abgelehnt: "mdi:close-circle",
  Anfrage: "mdi:email-outline",
  Modus: "mdi:tune-variant",
  Manuell: "mdi:hand-back-right",
  Fehler: "mdi:alert",
  Abgerechnet: "mdi:cash",
};

interface Entry {
  zeit: string;
  art: string;
  text: string;
  modus?: string;
  soc?: number;
  preis?: number;
}

const KIND_ICON: Record<string, string> = {
  user: "mdi:account",
  automation: "mdi:robot-outline",
  entity: "mdi:link-variant",
  service: "mdi:cog-outline",
  device: "mdi:cog-outline",
  external: "mdi:alert-circle-outline",
};
const RANGES = [
  [24, "24 h"],
  [72, "3 Tage"],
  [168, "7 Tage"],
] as const;

export class EmsLogView extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;
  @property({ attribute: false }) config!: AppConfig;
  @state() private hours = 24;
  @state() private logbook?: LogbookRow[];
  @state() private logbookError?: string;
  @state() private outages = false;
  private raw: Parameters<typeof logbookRows>[0] = [];

  static styles = [cardStyles];

  connectedCallback(): void {
    super.connectedCallback();
    this.loadLogbook();
  }

  private async loadLogbook(): Promise<void> {
    const ids = LOGBOOK_KEYS.map((k) => this.config.entitaeten[k]).filter(Boolean);
    this.logbookError = undefined;
    try {
      this.raw = await loadLogbook(this.hass, ids, this.hours);
      this.logbook = this.rows();
    } catch (e) {
      this.logbookError = (e as { message?: string }).message ?? String(e);
    }
  }

  private rows(): LogbookRow[] {
    const inverter = new Set(INVERTER_KEYS.map((k) => this.config.entitaeten[k]).filter(Boolean));
    return logbookRows(this.raw, this.hass.user?.id, this.outages, inverter);
  }

  private renderLogbook() {
    const rows = this.logbook ?? [];
    let lastDay = "";
    return html`<div class="card wide">
      <style>
        .ranges {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 6px;
          margin-bottom: 8px;
        }
        .ranges label {
          margin-left: auto;
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .ranges button {
          font: inherit;
          font-size: 0.85rem;
          border: 1px solid var(--divider-color);
          background: transparent;
          color: var(--primary-text-color);
          border-radius: 999px;
          padding: 4px 12px;
          cursor: pointer;
        }
        .ranges button.on {
          background: var(--primary-color);
          border-color: var(--primary-color);
          color: var(--text-primary-color, #fff);
        }
        .day td {
          font-weight: 600;
          padding-top: 14px;
          border-bottom: none;
        }
        tr.external td {
          background: color-mix(in srgb, var(--warning-color, #ffa600) 10%, transparent);
        }
        .by {
          display: inline-flex;
          align-items: center;
          gap: 4px;
        }
        .by ha-icon {
          --mdc-icon-size: 16px;
        }
      </style>
      <h2><ha-icon icon="mdi:format-list-bulleted"></ha-icon>Logbuch</h2>
      <p class="muted small">
        Jede Änderung an Modus, Soll, Wechselrichter und Freigaben – und wer sie ausgelöst hat. Gelb: Änderung am
        Wechselrichter ohne EMS-Befehl kurz davor – also von außen (z. B. Hersteller-App, Stromanbieter, von Hand).
      </p>
      <div class="ranges">
        ${RANGES.map(
          ([h, label]) =>
            html`<button
              class=${this.hours === h ? "on" : ""}
              @click=${() => {
                this.hours = h;
                this.loadLogbook();
              }}
            >
              ${label}
            </button>`,
        )}
        <label class="small muted"
          ><input
            type="checkbox"
            .checked=${this.outages}
            @change=${(ev: Event) => {
              this.outages = (ev.target as HTMLInputElement).checked;
              this.logbook = this.rows();
            }}
          />Verbindungsabbrüche zeigen</label
        >
      </div>
      ${this.logbookError ? html`<div class="warn error">${this.logbookError}</div>` : nothing}
      ${!this.logbook
        ? html`<div class="muted">Lädt …</div>`
        : rows.length
          ? html`<div class="scroll">
              <table>
                <tbody>
                  ${rows.map((r) => {
                    const day = new Date(r.ms).toLocaleDateString("de-DE", {
                      weekday: "long",
                      day: "2-digit",
                      month: "2-digit",
                    });
                    const header = day !== lastDay;
                    lastDay = day;
                    return html`${header ? html`<tr class="day"><td colspan="3">${day}</td></tr>` : nothing}
                      <tr class=${r.kind}>
                        <td style="white-space:nowrap">${time(r.ms)}</td>
                        <td>
                          <b>${r.name}</b> ${r.what}
                        </td>
                        <td class="small muted">
                          <span class="by"><ha-icon icon=${KIND_ICON[r.kind]}></ha-icon>${r.by}</span>
                        </td>
                      </tr>`;
                  })}
                </tbody>
              </table>
            </div>`
          : html`<div class="muted">Keine Änderungen in diesem Zeitraum.</div>`}
    </div>`;
  }

  render() {
    const log = configured(this.hass, this.config, "protokoll");
    const entries: Entry[] = log?.attributes.eintraege ?? [];
    return html`<div class="grid">
      <div class="card wide">
        <h2><ha-icon icon="mdi:clipboard-text-clock"></ha-icon>Entscheidungen</h2>
        ${!this.config.entitaeten.protokoll
          ? html`<p class="muted">
              Kein Protokoll eingerichtet (Entität für <code>protokoll</code> mit Attribut
              <code>eintraege</code>).
            </p>`
          : entries.length
            ? html`<p class="muted small">
                  Moduswechsel, Freigaben, Schaltbefehle und Fehler mit Akkustand und Preis, neueste oben.
                </p>
                <div class="scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Zeit</th>
                        <th>Art</th>
                        <th>Was</th>
                        <th class="num">Akku</th>
                        <th class="num">Preis</th>
                      </tr>
                    </thead>
                    <tbody>
                      ${entries.map((e, i) => {
                        const t = toMs(e.zeit);
                        const older = entries[i + 1];
                        const modeChanged = e.modus && (!older || older.modus !== e.modus);
                        return html`<tr>
                          <td style="white-space:nowrap">${t ? dateTime(t) : ""}</td>
                          <td style="white-space:nowrap">
                            <ha-icon
                              icon=${ICONS[e.art] ?? "mdi:circle-small"}
                              style="--mdc-icon-size:18px;color:${e.art === "Fehler"
                                ? "var(--error-color)"
                                : "var(--secondary-text-color)"}"
                            ></ha-icon>
                            ${e.art}
                          </td>
                          <td>${e.text}${modeChanged
                              ? html`<div class="muted small">Modus: ${e.modus}</div>`
                              : nothing}</td>
                          <td class="num">${e.soc ?? "–"} %</td>
                          <td class="num">${e.preis != null ? ct(e.preis) : "–"}</td>
                        </tr>`;
                      })}
                    </tbody>
                  </table>
                </div>`
            : html`<p class="muted">Noch keine Einträge.</p>`}
      </div>
      ${this.renderLogbook()}
      ${this.config.entitaeten.modus
        ? html`<div class="card">
            <h2><ha-icon icon="mdi:shield-lock"></ha-icon>Regeln der Steuerung</h2>
            <ul class="small">
              <li>Standard ist „Nur planen“.</li>
              <li>Sperren ab 30 min, Netzladen ab 15 min, nur Aktionen ab 5 ct Ersparnis.</li>
              <li>Sperren = Entladeleistung fast 0, die PV lädt weiter; danach zurück auf das Maximum.</li>
              <li>Plan älter als 30 min → Eigenverbrauch.</li>
              <li>Fremde Einstellung am Wechselrichter → das EMS greift nicht ein.</li>
              <li>Jeder Befehl wird nach 60 s geprüft, Fehler kommen per Push.</li>
            </ul>
          </div>`
        : nothing}
      ${this.renderDiagnosis()}
    </div>`;
  }

  private renderDiagnosis() {
    const rows = Object.entries(this.config.entitaeten)
      .map(([key, id]) => [key, id, this.hass.states[id]] as const)
      .sort(([a], [b]) => a.localeCompare(b));
    const sensors = Object.entries(this.config.sensoren).filter(([, id]) => id) as [string, string][];
    return html`<div class="card">
      <h2><ha-icon icon="mdi:stethoscope"></ha-icon>Diagnose</h2>
      <details>
        <summary>Alle Entitäten und ihr Zustand</summary>
        <div class="scroll">
          <table class="small">
            ${[...sensors.map(([k, id]) => [`sensor ${k}`, id, this.hass.states[id]] as const), ...rows].map(
              ([key, id, st]) =>
                html`<tr>
                  <td><code>${key}</code></td>
                  <td>${id}</td>
                  <td class=${!st || st.state === "unavailable" ? "" : "muted"}>
                    ${st ? st.state : html`<span style="color:var(--error-color)">fehlt</span>`}
                  </td>
                </tr>`,
            )}
          </table>
        </div>
      </details>
      <p class="muted small">Version ${this.config.version}</p>
    </div>`;
  }
}
define("ems-log-view", EmsLogView);
