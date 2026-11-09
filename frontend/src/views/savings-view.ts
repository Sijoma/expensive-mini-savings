import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define, type ChartOption } from "../chart";
import { eur, num, time, toMs } from "../format";
import { configured, dailyChanges, statisticChange } from "../ha";
import { savingsOption } from "../options";
import { ACTIONS, cardStyles } from "../theme";
import type { AppConfig, HomeAssistant } from "../types";

const REFRESH_MS = 10 * 60_000;
const DAYS = 30;

interface Booked {
  start: string;
  ende: string;
  was: string;
  kwh: number;
  betrag: number;
}

export class EmsSavingsView extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;
  @property({ attribute: false }) config!: AppConfig;
  @state() private periods: Record<string, number | null> = {};
  @state() private chart?: ChartOption;
  @state() private loaded = false;

  private timer?: number;

  static styles = [cardStyles];

  connectedCallback(): void {
    super.connectedCallback();
    this.load();
    this.timer = window.setInterval(() => this.load(), REFRESH_MS);
  }

  disconnectedCallback(): void {
    super.disconnectedCallback();
    window.clearInterval(this.timer);
  }

  private async load(): Promise<void> {
    const measured = this.config.entitaeten.ersparnis;
    const forecast = this.config.entitaeten.ersparnis_prognose;
    if (!measured) return;
    const [day, week, month, forecastMonth] = await Promise.all([
      statisticChange(this.hass, measured, "day"),
      statisticChange(this.hass, measured, "week"),
      statisticChange(this.hass, measured, "month"),
      forecast ? statisticChange(this.hass, forecast, "month") : Promise.resolve(null),
    ]);
    this.periods = { day, week, month, forecastMonth };
    const ids = [measured, ...(forecast ? [forecast] : [])];
    const daily = await dailyChanges(this.hass, ids, DAYS);
    this.chart = savingsOption(
      daily.days,
      daily.values[measured],
      forecast ? daily.values[forecast] : daily.days.map(() => null),
    );
    this.loaded = true;
  }

  render() {
    const savings = configured(this.hass, this.config, "ersparnis");
    if (!this.config.entitaeten.ersparnis) {
      return html`<div class="card">
        <h2><ha-icon icon="mdi:piggy-bank"></ha-icon>Ersparnis</h2>
        <p class="muted">
          Keine Abrechnung eingerichtet. Die App zeigt hier die gemessene Ersparnis, sobald in der Integration
          eine Entität für <code>ersparnis</code> gesetzt ist (Sensor in EUR mit Attribut
          <code>aktionen</code>).
        </p>
      </div>`;
    }
    const p = this.periods;
    const booked: Booked[] = savings?.attributes.aktionen ?? [];
    const total = booked.reduce((s, b) => s + (b.betrag || 0), 0);
    const since = savings?.attributes.seit;
    return html`<div class="grid">
      <div class="card wide">
        <h2><ha-icon icon="mdi:piggy-bank"></ha-icon>Gespart (gemessen)</h2>
        <div class="kpis">
          ${this.kpi(p.day, "Heute")} ${this.kpi(p.week, "Diese Woche")} ${this.kpi(p.month, "Diesen Monat")}
          ${this.kpi(savings ? Number(savings.state) : null, since ? `Seit ${since}` : "Seit Start")}
          ${this.config.entitaeten.ersparnis_prognose ? this.kpi(p.forecastMonth, "Prognose diesen Monat") : nothing}
        </div>
      </div>
      <div class="card wide">
        <h2><ha-icon icon="mdi:chart-bar"></ha-icon>Pro Tag</h2>
        ${this.chart
          ? html`<ems-chart .option=${this.chart} .height=${240}></ems-chart>`
          : html`<div class="muted">${this.loaded ? "Keine Daten." : "Lädt …"}</div>`}
      </div>
      <div class="card">
        <h2><ha-icon icon="mdi:receipt-text-check"></ha-icon>Abgerechnete Aktionen</h2>
        ${booked.length
          ? html`<div class="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Wann</th>
                    <th>Was</th>
                    <th class="num">Menge</th>
                    <th class="num">Gespart</th>
                  </tr>
                </thead>
                <tbody>
                  ${booked.map((b) => {
                    const a = ACTIONS[b.was] ?? ACTIONS.n;
                    const start = toMs(b.start);
                    const end = toMs(b.ende);
                    return html`<tr>
                      <td style="white-space:nowrap">
                        ${start ? new Date(start).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" }) : ""}
                        ${start ? time(start) : ""}–${end ? time(end) : ""}
                      </td>
                      <td><span class="pill" style="background:${a.color}">${a.short}</span></td>
                      <td class="num">${num(b.kwh, 1)} kWh</td>
                      <td class="num">${eur(b.betrag)}</td>
                    </tr>`;
                  })}
                  <tr>
                    <td></td>
                    <td><b>Zusammen</b></td>
                    <td></td>
                    <td class="num"><b>${eur(total)}</b></td>
                  </tr>
                </tbody>
              </table>
            </div>`
          : html`<div class="muted">
              Noch keine ausgeführte Aktion. Abgerechnet wird, sobald das EMS etwas geschaltet hat.
            </div>`}
      </div>
      <div class="card">
        <h2><ha-icon icon="mdi:scale-balance"></ha-icon>So wird gerechnet</h2>
        <ul class="small">
          <li>
            <b>Netzladen:</b> aus dem Netz geladene kWh × Vorteil je kWh nach Verlusten und Verschleiß.
          </li>
          <li><b>Sperren:</b> während der Sperre aus dem Netz bezogene kWh × (späterer Preis − Preis jetzt).</li>
          <li>Die Mengen sind gemessen, der spätere Preis stammt aus dem Plan.</li>
          <li><b>Prognose:</b> was der Plan jeden Tag für die nächsten 24 h erwartet hat.</li>
        </ul>
      </div>
    </div>`;
  }

  private kpi(v: number | null | undefined, label: string) {
    return html`<div class="kpi">
      <span class="value">${v == null || Number.isNaN(v) ? "–" : eur(v)}</span>
      <span class="label">${label}</span>
    </div>`;
  }
}
define("ems-savings-view", EmsSavingsView);
