import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define, type ChartOption } from "../chart";
import { day, eur, num, time, toMs } from "../format";
import { configured, dailyChanges, isoDay, statisticChange } from "../ha";
import { savingsOption } from "../options";
import { ACTIONS, COLORS, cardStyles, pillColor } from "../theme";
import type { AppConfig, HomeAssistant } from "../types";

const REFRESH_MS = 10 * 60_000;
const DAYS = 30;
const WINDOW_MS = 24 * 3_600_000;
const DAY_MS = 86_400_000;

export interface Booked {
  start: string;
  ende: string;
  was: string;
  kwh: number;
  betrag: number;
}

export interface Promise24h {
  zeit: string;
  wert: number;
}

export interface PromiseCheck {
  promised: number;
  measured: number;
  windows: number;
  from: number;
  to: number;
}

/** Promised against measured, only over finished 24 h windows: a forecast made at 14:05 covers the next
 * 24 h; it counts once that time is over, together with the blocks that started inside it. Corrections
 * are not part of it. */
export function promiseCheck(promises: Promise24h[], booked: Booked[], now: number): PromiseCheck | null {
  const done = promises
    .map((p) => ({ at: toMs(p.zeit), value: p.wert }))
    .filter((p): p is { at: number; value: number } => p.at != null && p.at + WINDOW_MS <= now)
    .sort((a, b) => a.at - b.at);
  if (!done.length) return null;
  const from = done[0].at;
  const to = done[done.length - 1].at + WINDOW_MS;
  const measured = booked
    .filter((b) => b.was !== "k")
    .filter((b) => {
      const start = toMs(b.start);
      return start != null && start >= from && start < to;
    })
    .reduce((s, b) => s + (b.betrag || 0), 0);
  const promised = done.reduce((s, p) => s + p.value, 0);
  return { promised, measured, windows: done.length, from, to };
}

/** Days since the start of counting (inclusive), at least 1. */
export function daysSince(since: string | undefined, now: number): number {
  const start = since ? Date.parse(`${since}T00:00:00`) : Number.NaN;
  if (Number.isNaN(start)) return 1;
  return Math.max(1, Math.floor((now - start) / DAY_MS) + 1);
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
    if (!measured) return;
    const [today, week, month] = await Promise.all([
      statisticChange(this.hass, measured, "day"),
      statisticChange(this.hass, measured, "week"),
      statisticChange(this.hass, measured, "month"),
    ]);
    this.periods = { today, week, month };
    const daily = await dailyChanges(this.hass, [measured], DAYS);
    // promised per day from the forecast's own list (starts again after a reset)
    const forecast = configured(this.hass, this.config, "ersparnis_prognose");
    const promises: Promise24h[] = forecast?.attributes.tage ?? [];
    const byDay = new Map<string, number>();
    for (const p of promises) {
      const at = toMs(p.zeit);
      if (at != null) byDay.set(isoDay(new Date(at)), (byDay.get(isoDay(new Date(at))) ?? 0) + p.wert);
    }
    const promised = daily.days.map((d) => byDay.get(d) ?? null);
    const any = [...daily.values[measured], ...promised].some((v) => v != null);
    this.chart = any ? savingsOption(daily.days, daily.values[measured], promised) : undefined;
    this.loaded = true;
  }

  render() {
    const savings = configured(this.hass, this.config, "ersparnis");
    if (!this.config.entitaeten.ersparnis) {
      return html`<section class="card">
        <h2><ha-icon icon="mdi:piggy-bank"></ha-icon>Ersparnis</h2>
        <p class="muted">Keine Abrechnung eingerichtet.</p>
      </section>`;
    }
    const now = Date.now();
    const total = savings ? Number(savings.state) : 0;
    const since: string | undefined = savings?.attributes.seit;
    const days = daysSince(since, now);
    const booked: Booked[] = ((savings?.attributes.aktionen ?? []) as Booked[]).filter(
      (b) => Math.abs(b.kwh) >= 0.05 || Math.abs(b.betrag) >= 0.005,
    );
    const forecast = configured(this.hass, this.config, "ersparnis_prognose");
    const check = promiseCheck(forecast?.attributes.tage ?? [], booked, now);
    const p = this.periods;
    return html`<style>
        .hero {
          display: flex;
          flex-wrap: wrap;
          align-items: baseline;
          gap: 4px 16px;
        }
        .hero .big {
          font-size: 3rem;
          font-weight: 600;
          letter-spacing: -0.02em;
          line-height: 1.1;
          color: ${COLORS.measured};
          font-variant-numeric: tabular-nums;
        }
        .hero .what {
          font-size: 1.1rem;
        }
        .facts {
          display: flex;
          flex-wrap: wrap;
          gap: 6px 24px;
          margin: 14px 0 0;
          padding: 0;
          list-style: none;
          color: var(--secondary-text-color);
          font-variant-numeric: tabular-nums;
        }
        .facts b {
          color: var(--primary-text-color);
        }
        .compare {
          font-size: 1.1rem;
          line-height: 1.5;
          margin: 0 0 12px;
          max-width: 70ch;
        }
        .ratio {
          position: relative;
          height: 12px;
          border-radius: 6px;
          background: color-mix(in srgb, var(--primary-text-color) 10%, transparent);
          max-width: 480px;
        }
        .ratio .fill {
          position: absolute;
          inset: 0 auto 0 0;
          border-radius: 6px;
          background: ${COLORS.measured};
        }
        .legend {
          display: flex;
          flex-wrap: wrap;
          gap: 4px 14px;
          margin: 4px 0 0;
          padding: 0;
          list-style: none;
        }
        .legend li {
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .legend i {
          width: 10px;
          height: 10px;
          border-radius: 3px;
        }
      </style>
      <div class="grid">
        <section class="card wide" aria-labelledby="sav-title">
          <h2 id="sav-title"><ha-icon icon="mdi:piggy-bank"></ha-icon>Gespart</h2>
          <div class="hero">
            <span class="big">${eur(total)}</span>
            <span class="what">
              seit ${since ? day(since) : "Start"} · ${days} ${days === 1 ? "Tag" : "Tage"} · Ø ${eur(total / days)} pro Tag
            </span>
          </div>
          <p class="muted small" style="margin:6px 0 0">
            Gemessen, nach Verlusten und Verschleiß: was der Akku gegenüber „ohne Steuerung“ gespart hat.
          </p>
          <ul class="facts small">
            <li>heute <b>${p.today == null ? "–" : eur(p.today)}</b></li>
            <li>diese Woche <b>${p.week == null ? "–" : eur(p.week)}</b></li>
            <li>dieser Monat <b>${p.month == null ? "–" : eur(p.month)}</b></li>
          </ul>
        </section>

        <section class="card wide" aria-labelledby="promise-title">
          <h2 id="promise-title"><ha-icon icon="mdi:scale-balance"></ha-icon>Hält der Plan, was er verspricht?</h2>
          ${check
            ? html`<p class="compare">
                  In ${check.windows} ${check.windows === 1 ? "Tag" : "Tagen"} (${day(new Date(check.from).toISOString())}
                  bis ${day(new Date(check.to).toISOString())}) hat der Plan <b>${eur(check.promised)}</b> versprochen,
                  gemessen wurden <b>${eur(check.measured)}</b>${check.promised > 0
                    ? html` (<b>${Math.round((check.measured / check.promised) * 100)} %</b>)`
                    : nothing}.
                </p>
                ${check.promised > 0
                  ? html`<div
                      class="ratio"
                      role="img"
                      aria-label="${Math.round((check.measured / check.promised) * 100)} Prozent des Versprochenen"
                    >
                      <div class="fill" style="width:${Math.min(100, (check.measured / check.promised) * 100)}%"></div>
                    </div>`
                  : nothing}
                <p class="muted small">
                  Liegt das über Wochen nahe 100 %, rechnet der Planer realistisch. Deutlich darunter heißt: Er ist zu
                  optimistisch (z. B. Verbrauch oder PV anders als erwartet, oder Aktionen wurden abgelehnt).
                </p>`
            : html`<p class="compare">
                Der Vergleich beginnt, sobald der erste 24-Stunden-Zeitraum seit dem
                ${forecast?.attributes.seit ? day(forecast.attributes.seit) : "Start"} vorbei ist (die Prognose wird
                täglich um 14:05 gemacht).
              </p>`}
        </section>

        <section class="card wide" aria-labelledby="day-title">
          <h2 id="day-title"><ha-icon icon="mdi:chart-bar"></ha-icon>Pro Tag</h2>
          ${this.chart
            ? html`<ems-chart
                  .option=${this.chart}
                  .height=${240}
                  .label=${"Ersparnis pro Tag in Euro: gemessen und vom Plan versprochen."}
                ></ems-chart>
                <ul class="legend small muted" aria-label="Legende">
                  <li><i style="background:${COLORS.measured}"></i>Gemessen</li>
                  <li><i style="background:${COLORS.forecast}"></i>Vom Plan versprochen (um 14:05 für die nächsten 24 h)</li>
                </ul>`
            : html`<p class="muted">
                ${this.loaded ? "Noch keine Tageswerte. Sie erscheinen nach dem ersten gebuchten Tag." : "Lädt …"}
              </p>`}
        </section>

        <section class="card wide" aria-labelledby="booked-title">
          <h2 id="booked-title"><ha-icon icon="mdi:receipt-text-check"></ha-icon>Abgerechnete Aktionen</h2>
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
                          ${start
                            ? new Date(start).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })
                            : ""}
                          ${b.was === "k" ? "" : html`${start ? time(start) : ""}–${end ? time(end) : ""}`}
                        </td>
                        <td>
                          ${b.was === "k"
                            ? html`<span class="pill">Korrektur</span>`
                            : html`<span class="pill" style=${pillColor(b.was)}>${a.name}</span>`}
                        </td>
                        <td class="num">${b.was === "k" ? "" : `${num(b.kwh, 1)} kWh`}</td>
                        <td class="num">${eur(b.betrag)}</td>
                      </tr>`;
                    })}
                  </tbody>
                </table>
              </div>`
            : html`<p class="muted">Noch keine ausgeführte Aktion. Abgerechnet wird, sobald das EMS etwas geschaltet hat.</p>`}
          <details>
            <summary>So wird gerechnet</summary>
            <ul class="small" style="line-height:1.5">
              <li>
                Jede ausgeführte Aktion bekommt die Ersparnis, die der Plan für sie ausgerechnet hat: Plan mit gegen Plan
                ohne diese Aktion, nach Verlusten und Verschleiß.
              </li>
              <li>
                Anteilig nach der gemessenen Menge (geladene kWh bzw. Netzbezug während der Sperre), höchstens der
                geplante Betrag. Lief eine Aktion kürzer, zählt entsprechend weniger.
              </li>
              <li>
                Versprochen: was der Plan jeden Tag um 14:05 für die nächsten 24 Stunden erwartet. Verglichen wird nur,
                was schon vorbei ist.
              </li>
            </ul>
          </details>
        </section>
      </div>`;
  }
}
define("ems-savings-view", EmsSavingsView);
