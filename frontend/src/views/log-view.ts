import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define } from "../chart";
import { ct, dateTime, time, toMs } from "../format";
import { configured, sensor } from "../ha";
import { INVERTER_KEYS, LOGBOOK_KEYS, loadLogbook, logbookRows, type LogbookRow } from "../logbook";
import { collapse, plainState, plainText } from "../texts";
import { cardStyles } from "../theme";
import type { AppConfig, HomeAssistant } from "../types";

const ICONS: Record<string, string> = {
  Geschaltet: "mdi:flash",
  Freigabe: "mdi:check-circle-outline",
  Abgelehnt: "mdi:close-circle-outline",
  Anfrage: "mdi:email-outline",
  Modus: "mdi:tune-variant",
  Manuell: "mdi:hand-back-right-outline",
  Fehler: "mdi:alert-outline",
  Hinweis: "mdi:information-outline",
  Abgerechnet: "mdi:cash",
};

/** Who is behind an entry of the executor's log. */
const ACTOR: Record<string, string> = {
  Geschaltet: "EMS",
  Abgerechnet: "EMS",
  Anfrage: "EMS",
  Fehler: "EMS",
  Freigabe: "Du",
  Abgelehnt: "Du",
  Modus: "Du",
  Manuell: "von außen",
  Hinweis: "EMS",
};
const ACTOR_FILTERS = ["Alle", "Du", "EMS", "von außen"] as const;

export interface Entry {
  zeit: string;
  art: string;
  text: string;
  modus?: string;
  soc?: number;
  preis?: number;
}

/** Entries for display: plain text, bookings of nothing left out, equal neighbors collapsed. */
export function decisions(entries: Entry[], actor: string): { item: Entry; count: number }[] {
  const cleaned = entries
    .map((e) => ({ ...e, text: plainText(e.text) }))
    .filter((e) => !(e.art === "Abgerechnet" && /\b0,0 kWh/.test(e.text) && /\b0,00 €/.test(e.text)))
    .filter((e) => actor === "Alle" || (ACTOR[e.art] ?? "EMS") === actor);
  return collapse(cleaned, (e) => `${e.art}|${e.text}`);
}

const KIND_ICON: Record<string, string> = {
  user: "mdi:account-outline",
  automation: "mdi:robot-outline",
  entity: "mdi:link-variant",
  service: "mdi:cog-outline",
  device: "mdi:cog-outline",
  external: "mdi:alert-circle-outline",
};
const RANGES = [
  [24, "24 Stunden"],
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
  @state() private actor: string = "Alle";
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
      const message = (e as { message?: string }).message ?? String(e);
      this.logbookError = `Das Logbuch konnte nicht geladen werden (${message}). Seite neu laden oder später erneut versuchen.`;
    }
  }

  private rows(): LogbookRow[] {
    const inverter = new Set(INVERTER_KEYS.map((k) => this.config.entitaeten[k]).filter(Boolean));
    return logbookRows(this.raw, this.hass.user?.id, this.outages, inverter, this.config.entitaeten.soll);
  }

  render() {
    return html`<style>
        .chips {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 6px;
          margin-bottom: 8px;
        }
        .chips button {
          font: inherit;
          font-size: 0.875rem;
          min-height: 36px;
          border: 1px solid var(--divider-color);
          background: transparent;
          color: var(--primary-text-color);
          border-radius: 999px;
          padding: 0 14px;
          cursor: pointer;
        }
        .chips button[aria-pressed="true"] {
          background: var(--ems-accent);
          border-color: var(--ems-accent);
          color: #fff;
        }
        .chips label {
          margin-left: auto;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          min-height: 36px;
        }
        .entries {
          list-style: none;
          margin: 0;
          padding: 0;
        }
        .entries li {
          display: grid;
          grid-template-columns: 7.5rem 1fr;
          gap: 2px 12px;
          padding: 10px 0;
          border-bottom: 1px solid var(--divider-color);
        }
        .entries .meta {
          display: flex;
          flex-wrap: wrap;
          gap: 2px 10px;
          align-items: center;
        }
        .entries .kind {
          display: inline-flex;
          align-items: center;
          gap: 4px;
          font-weight: 600;
        }
        .entries ha-icon {
          --mdc-icon-size: 18px;
          color: var(--secondary-text-color);
        }
        .entries li.error ha-icon {
          color: var(--error-color);
        }
        .entries li.external {
          background: color-mix(in srgb, var(--warning-color, #ffa600) 12%, transparent);
          margin: 0 -8px;
          padding-left: 8px;
          padding-right: 8px;
          border-radius: 8px;
        }
        .entries .day {
          grid-template-columns: 1fr;
          font-weight: 600;
          padding-top: 16px;
          border-bottom: none;
        }
        @media (max-width: 600px) {
          .entries li {
            grid-template-columns: 1fr;
          }
        }
        code {
          font-size: 0.85em;
        }
      </style>
      <div class="grid">
        ${this.renderDecisions()} ${this.renderLogbook()} ${this.renderRules()} ${this.renderDiagnosis()}
      </div>`;
  }

  private renderDecisions() {
    const log = configured(this.hass, this.config, "protokoll");
    const entries: Entry[] = log?.attributes.eintraege ?? [];
    if (!this.config.entitaeten.protokoll) {
      return html`<section class="card wide">
        <h2><ha-icon icon="mdi:clipboard-text-clock-outline"></ha-icon>Entscheidungen</h2>
        <p class="muted">
          Kein Protokoll eingerichtet. Sobald in der Integration eine Entität für <code>protokoll</code> gesetzt ist
          (Sensor mit Attribut <code>eintraege</code>), stehen hier Freigaben, Schaltbefehle und Fehler.
        </p>
      </section>`;
    }
    const shown = decisions(entries, this.actor);
    return html`<section class="card wide" aria-labelledby="dec-title">
      <h2 id="dec-title"><ha-icon icon="mdi:clipboard-text-clock-outline"></ha-icon>Entscheidungen</h2>
      <p class="muted small" style="margin-top:0">
        Was entschieden und geschaltet wurde, mit Akkustand und Strompreis in dem Moment. Neueste oben.
      </p>
      <div class="chips" role="group" aria-label="Nach Auslöser filtern">
        ${ACTOR_FILTERS.map(
          (a) =>
            html`<button aria-pressed=${this.actor === a} @click=${() => (this.actor = a)}>${a}</button>`,
        )}
      </div>
      ${shown.length
        ? html`<ul class="entries">
            ${shown.map(({ item: e, count }, i) => {
              const t = toMs(e.zeit);
              const older = shown[i + 1]?.item;
              const modeChanged = e.modus && older && older.modus !== e.modus;
              return html`<li class=${e.art === "Fehler" ? "error" : ""}>
                <span class="muted num-tab">${t ? dateTime(t) : ""}</span>
                <span class="meta">
                  <span class="kind"><ha-icon icon=${ICONS[e.art] ?? "mdi:circle-small"}></ha-icon>${e.art}</span>
                  <span class="muted small">${ACTOR[e.art] ?? "EMS"}${count > 1 ? ` · ${count}× hintereinander` : ""}</span>
                </span>
                <span></span>
                <span>${e.text}</span>
                <span></span>
                <span class="muted small num-tab"
                  >Akku ${e.soc ?? "–"} % · Strom ${e.preis != null ? ct(e.preis) : "–"}${modeChanged
                    ? ` · ab hier Modus „${e.modus}“`
                    : ""}</span
                >
              </li>`;
            })}
          </ul>`
        : html`<p class="muted">
            ${entries.length ? "Keine Einträge für diesen Filter." : "Noch keine Einträge. Sie entstehen, sobald das EMS etwas anfragt oder schaltet."}
          </p>`}
    </section>`;
  }

  private renderLogbook() {
    const rows = this.logbook ?? [];
    let lastDay = "";
    return html`<section class="card wide" aria-labelledby="logbook-title">
      <h2 id="logbook-title"><ha-icon icon="mdi:format-list-bulleted"></ha-icon>Logbuch</h2>
      <p class="muted small" style="margin-top:0">
        Jede Änderung an Modus, Soll, Wechselrichter und Freigaben – und wer sie ausgelöst hat. Hervorgehoben und mit
        „von außen“ markiert: Änderungen am Wechselrichter, die nicht vom EMS kamen (z. B. Hersteller-App,
        Stromanbieter, von Hand).
      </p>
      <div class="chips" role="group" aria-label="Zeitraum">
        ${RANGES.map(
          ([h, label]) =>
            html`<button
              aria-pressed=${this.hours === h}
              @click=${() => {
                this.hours = h;
                this.loadLogbook();
              }}
            >
              ${label}
            </button>`,
        )}
        <label class="small"
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
      ${!this.logbook && !this.logbookError
        ? html`<p class="muted">Lädt …</p>`
        : rows.length
          ? html`<ul class="entries">
              ${rows.map((r) => {
                const day = new Date(r.ms).toLocaleDateString("de-DE", {
                  weekday: "long",
                  day: "2-digit",
                  month: "2-digit",
                });
                const header = day !== lastDay;
                lastDay = day;
                return html`${header ? html`<li class="day">${day}</li>` : nothing}
                  <li class=${r.kind}>
                    <span class="muted num-tab">${time(r.ms)}</span>
                    <span><b>${r.name}</b> ${r.what}</span>
                    <span></span>
                    <span class="muted small meta"
                      ><ha-icon icon=${KIND_ICON[r.kind]}></ha-icon>${r.by}</span
                    >
                  </li>`;
              })}
            </ul>`
          : this.logbookError
            ? nothing
            : html`<p class="muted">Keine Änderungen in diesem Zeitraum.</p>`}
    </section>`;
  }

  private renderRules() {
    if (!this.config.entitaeten.modus) return nothing;
    return html`<section class="card" aria-labelledby="rules-title">
      <h2 id="rules-title"><ha-icon icon="mdi:shield-lock-outline"></ha-icon>Regeln der Steuerung</h2>
      <ul class="small" style="line-height:1.5;padding-left:1.2em;margin:0">
        <li>Ohne deine Entscheidung gilt „Nur planen“.</li>
        <li>Gefragt wird nur bei Aktionen, die mindestens 5 ct sparen.</li>
        <li>„Entladung gesperrt“ heißt: Der Akku gibt nichts ab, die PV lädt ihn weiter.</li>
        <li>Ist der Plan älter als 30 Minuten, läuft der Akku normal.</li>
        <li>Stellt jemand anderes den Wechselrichter um, greift das EMS nicht ein.</li>
        <li>Jeder Schaltbefehl wird nach einer Minute geprüft; Fehler kommen als Nachricht aufs Handy.</li>
      </ul>
    </section>`;
  }

  private renderDiagnosis() {
    const rows = Object.entries(this.config.entitaeten)
      .map(([key, id]) => [key, id, this.hass.states[id]] as const)
      .sort(([a], [b]) => a.localeCompare(b));
    const sensors = Object.entries(this.config.sensoren).filter(([, id]) => id) as [string, string][];
    const plan = sensor(this.hass, this.config, "plan");
    const computed = toMs(plan?.attributes.berechnet);
    return html`<section class="card" aria-labelledby="diag-title">
      <h2 id="diag-title"><ha-icon icon="mdi:stethoscope"></ha-icon>Diagnose</h2>
      <p class="small" style="margin-top:0">
        Letzter Plan: ${computed ? dateTime(computed) : "–"}, Rechenzeit ${plan?.attributes.rechenzeit_ms ?? "–"} ms ·
        Version ${this.config.version}
      </p>
      <details>
        <summary>Alle Entitäten und ihr Zustand</summary>
        <div class="scroll">
          <table class="small">
            ${[...sensors.map(([k, id]) => [`sensor ${k}`, id, this.hass.states[id]] as const), ...rows].map(
              ([key, id, st]) =>
                html`<tr>
                  <td><code>${key}</code></td>
                  <td>${id}</td>
                  <td>
                    ${st
                      ? st.state === "unavailable" || st.state === "unknown"
                        ? html`<b>${plainState(st.state)}</b>`
                        : html`<span class="muted">${st.state}</span>`
                      : html`<b style="color:var(--error-color)">fehlt</b>`}
                  </td>
                </tr>`,
            )}
          </table>
        </div>
      </details>
    </section>`;
  }
}
define("ems-log-view", EmsLogView);
