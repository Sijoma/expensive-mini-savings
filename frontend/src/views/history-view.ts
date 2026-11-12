import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define, type ChartOption } from "../chart";
import { ct, eur, num } from "../format";
import { isoDay } from "../ha";
import { historyOption } from "../options";
import { ACTIONS, COLORS, cardStyles } from "../theme";
import type { AppConfig, HistoryDay, HistoryRow, HomeAssistant } from "../types";

/** Measured codes that count as the planned one (old-style Stop is a hold too). */
const SAME: Record<string, string> = { f: "h" };

export function summary(rows: HistoryRow[]) {
  const withPlan = rows.filter((r) => r.plan_aktion && r.aktion);
  const same = withPlan.filter((r) => (SAME[r.aktion!] ?? r.aktion) === r.plan_aktion).length;
  const importKwh = rows.reduce((s, r) => s + (r.bezug_kwh ?? 0), 0);
  const cost = rows.reduce((s, r) => s + ((r.bezug_kwh ?? 0) * (r.preis_ct ?? 0)) / 100, 0);
  const avg = importKwh > 0 ? (cost * 100) / importKwh : null;
  return { quarters: rows.length, withPlan: withPlan.length, same, importKwh, cost, avg };
}

export class EmsHistoryView extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;
  @property({ attribute: false }) config!: AppConfig;
  @state() private day = isoDay(new Date());
  @state() private data?: HistoryDay;
  @state() private error?: string;
  @state() private chart?: ChartOption;

  static styles = [cardStyles];

  connectedCallback(): void {
    super.connectedCallback();
    this.load();
  }

  private async load(): Promise<void> {
    this.error = undefined;
    try {
      this.data = await this.hass.callWS<HistoryDay>({ type: "ems/history", tag: this.day });
      this.chart = historyOption(this.data.viertelstunden);
    } catch (e) {
      this.error = (e as { message?: string }).message ?? String(e);
    }
  }

  private move(days: number): void {
    const d = new Date(`${this.day}T12:00:00`);
    d.setDate(d.getDate() + days);
    this.day = isoDay(d);
    this.load();
  }

  render() {
    const rows = this.data?.viertelstunden ?? [];
    const s = summary(rows);
    const today = isoDay(new Date());
    const first = this.data?.tage?.[0];
    const label = new Date(`${this.day}T12:00:00`).toLocaleDateString("de-DE", {
      weekday: "long",
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    });
    return html`<div class="grid">
      <div class="card wide">
        <style>
          .nav {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
          }
          .nav .day {
            flex: 1;
            text-align: center;
            font-weight: 600;
          }
          .nav button {
            font: inherit;
            background: transparent;
            border: 1px solid var(--divider-color);
            color: var(--primary-text-color);
            border-radius: 999px;
            width: 44px;
            height: 44px;
            cursor: pointer;
            display: grid;
            place-items: center;
          }
          .nav button:disabled {
            opacity: 0.4;
            cursor: default;
          }
        </style>
        <h2><ha-icon icon="mdi:history"></ha-icon>Plan und Wirklichkeit</h2>
        <div class="nav">
          <button aria-label="Tag zurück" ?disabled=${!!first && this.day <= first} @click=${() => this.move(-1)}>
            <ha-icon icon="mdi:chevron-left"></ha-icon>
          </button>
          <span class="day">${label}</span>
          <button aria-label="Tag vor" ?disabled=${this.day >= today} @click=${() => this.move(1)}>
            <ha-icon icon="mdi:chevron-right"></ha-icon>
          </button>
        </div>
        ${this.error ? html`<div class="warn error">${this.error}</div>` : nothing}
        ${rows.length
          ? html`<ems-chart
                .option=${this.chart}
                .height=${360}
                .label=${`Plan und Wirklichkeit am ${label}: ${s.same} von ${s.withPlan} Viertelstunden liefen wie geplant, Netzbezug ${num(s.importKwh, 1)} kWh.`}
              ></ems-chart>
              <div class="small muted" style="display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:4px">
                ${["n", "h", "l", "f", "e", "v"]
                  .filter((c) => rows.some((r) => r.aktion === c || r.plan_aktion === c))
                  .map(
                  (c) =>
                    html`<span
                      ><i
                        style="display:inline-block;width:10px;height:10px;border-radius:3px;background:${ACTIONS[c]
                          .color}"
                      ></i>
                      ${ACTIONS[c].name}</span
                    >`,
                )}
                <span
                  ><i style="display:inline-block;width:10px;height:3px;background:${COLORS.soc}"></i> Akku gemessen</span
                >
                ${rows.some((r) => r.plan_soc != null)
                  ? html`<span
                      ><i
                        style="display:inline-block;width:10px;height:0;border-top:2px dashed ${COLORS.soc}"
                      ></i>
                      Akku laut Plan</span
                    >`
                  : nothing}
                <span
                  ><i
                    style="display:inline-block;width:18px;height:10px;border-radius:3px;background:linear-gradient(90deg, ${COLORS.cheap}, ${COLORS.middle}, ${COLORS.expensive})"
                  ></i>
                  Preis von günstig bis teuer</span
                >
              </div>`
          : html`<div class="muted">
              Für diesen Tag gibt es keine Messung. Die Integration zeichnet jede Viertelstunde auf, wenn
              <code>messung</code> eingeschaltet ist.
            </div>`}
      </div>
      ${rows.length
        ? html`<div class="card wide">
            <h2><ha-icon icon="mdi:check-decagram-outline"></ha-icon>Zusammenfassung</h2>
            <div class="kpis">
              <div class="kpi">
                <span class="value">${s.withPlan ? `${Math.round((s.same / s.withPlan) * 100)} %` : "–"}</span>
                <span class="label">wie geplant (${s.same} von ${s.withPlan} Viertelstunden)</span>
              </div>
              <div class="kpi">
                <span class="value">${num(s.importKwh, 1)} kWh</span>
                <span class="label">Netzbezug</span>
              </div>
              <div class="kpi">
                <span class="value">${eur(s.cost)}</span>
                <span class="label">Kosten Netzbezug</span>
              </div>
              <div class="kpi">
                <span class="value">${s.avg == null ? "–" : ct(s.avg)}</span>
                <span class="label">Ø Preis bezogen</span>
              </div>
            </div>
            <p class="muted small">
              „Geplant“ ist, was der Plan kurz vor der jeweiligen Viertelstunde vorgesehen hatte. Abweichungen sind
              normal, wenn du anders entschieden hast (Freigabe, Modus) oder etwas anderes den Akku gesteuert hat.
            </p>
          </div>`
        : nothing}
    </div>`;
  }
}
define("ems-history-view", EmsHistoryView);
