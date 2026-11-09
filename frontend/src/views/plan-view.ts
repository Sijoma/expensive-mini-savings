import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { define, type ChartOption } from "../chart";
import { ct, eur, num, time, toMs, when } from "../format";
import { available, configured, sensor } from "../ha";
import { planOption } from "../options";
import { ACTIONS, COLORS, NAME_TO_CODE, cardStyles, cssVar } from "../theme";
import type { AppConfig, HomeAssistant, PlanBlock, SlotRow } from "../types";

const MODES = ["Nur planen", "Mit Freigabe", "Automatisch"];
const MIN_SAVING = 0.05;

export class EmsPlanView extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;
  @property({ attribute: false }) config!: AppConfig;
  @property({ type: Boolean }) narrow = false;
  @state() private busy = false;

  private chartFor?: unknown;
  private chartOption?: ChartOption;

  static styles = [cardStyles];

  private option(slots: SlotRow[]): ChartOption {
    // recompute only when the plan changed (hass changes on every state change in the house)
    if (this.chartFor !== slots) {
      this.chartFor = slots;
      this.chartOption = planOption(slots, Date.now(), cssVar(this, "--primary-color", "#03a9f4"));
    }
    return this.chartOption!;
  }

  render() {
    const plan = sensor(this.hass, this.config, "plan");
    const control = this.renderControl();
    return html`<style>
        .stack {
          display: grid;
          gap: 16px;
        }
        .row {
          display: grid;
          gap: 16px;
          grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
        }
        .row.single {
          grid-template-columns: minmax(0, 1fr);
        }
        @media (max-width: 900px) {
          .row {
            grid-template-columns: minmax(0, 1fr);
          }
        }
      </style>
      <div class="stack">
        <div class="row ${control === nothing ? "single" : ""}">${this.renderNow()} ${control}</div>
        <div class="card">${this.renderChart(plan?.attributes.slots ?? [])}</div>
        <div class="row">${this.renderBlocks()}</div>
      </div>`;
  }

  private renderNow() {
    const plan = sensor(this.hass, this.config, "plan");
    const value = sensor(this.hass, this.config, "wert");
    const hp = sensor(this.hass, this.config, "waermepumpe");
    const soc = configured(this.hass, this.config, "soc");
    const priceNow = configured(this.hass, this.config, "preis_jetzt");
    if (!available(plan)) {
      return html`<div class="card wide">
        <h2><ha-icon icon="mdi:battery-alert"></ha-icon>Jetzt</h2>
        <div class="warn error">
          <b>Planer nicht verfügbar.</b> Es fehlen Preise oder der Akkustand; die Steuerung fällt auf
          Eigenverbrauch zurück.
        </div>
      </div>`;
    }
    const a = plan.attributes;
    const code = NAME_TO_CODE[plan.state] ?? "n";
    const action = ACTIONS[code];
    const computed = toMs(a.berechnet);
    const price = a.slots?.[0]?.[1];
    return html`<div class="card now">
      <style>
        .now .head {
          display: flex;
          gap: 16px;
          align-items: center;
        }
        .now .badge {
          width: 56px;
          height: 56px;
          border-radius: 16px;
          display: grid;
          place-items: center;
          flex: none;
          color: #fff;
        }
        .now .badge ha-icon {
          --mdc-icon-size: 32px;
        }
        .now .state {
          font-size: 1.4rem;
          font-weight: 600;
        }
        .now .reason {
          margin: 12px 0 16px;
          line-height: 1.45;
        }
      </style>
      <div class="head">
        <div class="badge" style="background:${action.color}"><ha-icon icon=${action.icon}></ha-icon></div>
        <div>
          <div class="state">${plan.state}</div>
          <div class="muted small">
            Plan bis ${a.bis ? when(toMs(a.bis)!) : "–"} · berechnet ${computed ? time(computed) : "–"} in
            ${a.rechenzeit_ms ?? "–"} ms
          </div>
        </div>
      </div>
      <div class="reason">${a.grund}</div>
      <div class="kpis">
        <div class="kpi">
          <span class="value">${available(soc) ? `${Math.round(Number(soc.state))} %` : "–"}</span>
          <span class="label">Akku</span>
        </div>
        <div class="kpi">
          <span class="value">${ct(price)}</span>
          <span class="label">Preis jetzt${available(priceNow) ? "" : " (Plan)"}</span>
        </div>
        <div class="kpi">
          <span class="value" style="color:${COLORS.value}">${available(value) ? ct(value.state) : "–"}</span>
          <span class="label">1 kWh im Akku wert</span>
        </div>
        <div class="kpi">
          <span class="value">${eur(a.ersparnis_eur)}</span>
          <span class="label">Plan spart (${eur(a.kosten_plan_eur)} statt ${eur(a.kosten_ohne_eur)})</span>
        </div>
        <div class="kpi">
          <span class="value">${num(a.netzladen_kwh, 1)} kWh</span>
          <span class="label">aus dem Netz laden</span>
        </div>
        <div class="kpi">
          <span class="value">${available(hp) ? hp.state : "–"}</span>
          <span class="label">Wärmepumpe</span>
        </div>
      </div>
    </div>`;
  }

  private async call(domain: string, service: string, data: Record<string, unknown>) {
    this.busy = true;
    try {
      await this.hass.callService(domain, service, data);
    } finally {
      this.busy = false;
    }
  }

  private renderControl() {
    const mode = configured(this.hass, this.config, "modus");
    if (!mode) return nothing;
    const target = configured(this.hass, this.config, "soll");
    const inverter = configured(this.hass, this.config, "ems_modus");
    const approval = configured(this.hass, this.config, "freigabe");
    const until = configured(this.hass, this.config, "freigabe_bis");
    const pending: PlanBlock[] = approval?.state === "on" ? (approval.attributes.aktionen ?? []) : [];
    const untilMs = until ? toMs(until.state.replace(" ", "T")) : null;
    const options: string[] = mode.attributes.options ?? MODES;
    const ist: string | undefined = target?.attributes.ist;
    const approve = this.config.entitaeten.freigeben;
    const reject = this.config.entitaeten.ablehnen;
    return html`<div class="card control">
      <style>
        .modes {
          display: flex;
          border: 1px solid var(--divider-color);
          border-radius: 999px;
          overflow: hidden;
          margin-bottom: 12px;
        }
        .modes button {
          flex: 1;
          font: inherit;
          font-size: 0.9rem;
          border: none;
          background: transparent;
          color: var(--primary-text-color);
          padding: 8px 6px;
          cursor: pointer;
        }
        .modes button.on {
          background: var(--primary-color);
          color: var(--text-primary-color, #fff);
        }
        .rows {
          display: grid;
          grid-template-columns: auto 1fr;
          gap: 6px 12px;
          margin-bottom: 8px;
        }
        .pending {
          margin: 12px 0 0;
          padding: 0;
          list-style: none;
        }
        .pending li {
          padding: 8px 0;
          border-top: 1px solid var(--divider-color);
        }
        .buttons {
          display: flex;
          gap: 8px;
          margin-top: 12px;
          flex-wrap: wrap;
        }
      </style>
      <h2><ha-icon icon="mdi:battery-lock"></ha-icon>Steuerung</h2>
      <div class="modes" role="radiogroup" aria-label="Modus">
        ${options.map(
          (o) =>
            html`<button
              class=${o === mode.state ? "on" : ""}
              role="radio"
              aria-checked=${o === mode.state}
              ?disabled=${this.busy}
              @click=${() =>
                o !== mode.state &&
                this.call(mode.entity_id.split(".")[0], "select_option", { entity_id: mode.entity_id, option: o })}
            >
              ${o}
            </button>`,
        )}
      </div>
      <div class="muted small" style="margin-bottom:12px">
        ${mode.state === "Nur planen"
          ? "Nur Planung – es wird nichts geschaltet."
          : mode.state === "Mit Freigabe"
            ? "Schaltet nur, was du freigibst."
            : "Der Plan wird automatisch ausgeführt."}
      </div>
      <div class="rows small">
        ${target ? html`<span class="muted">EMS will</span><span>${target.state}</span>` : nothing}
        ${inverter ? html`<span class="muted">Wechselrichter</span><span>${inverter.state}</span>` : nothing}
        ${untilMs && untilMs > Date.now()
          ? html`<span class="muted">Freigegeben bis</span><span>${when(untilMs)}</span>`
          : nothing}
      </div>
      ${ist && ist.startsWith("Anders")
        ? html`<div class="warn">Wechselrichter: ${ist} – das EMS hält sich raus.</div>`
        : nothing}
      ${target && target.attributes.plan_aktuell === false
        ? html`<div class="warn error">Plan veraltet – EMS fällt auf Eigenverbrauch zurück.</div>`
        : nothing}
      ${pending.length
        ? html`<b>Wartet auf Freigabe</b>
            <ul class="pending small">
              ${pending.map(
                (x) =>
                  html`<li>
                    <b>${when(x.von)}–${time(x.bis)} ${ACTIONS[x.was].name}</b>
                    ${x.was === "l" ? ` ${num(x.kwh, 1)} kWh` : ""} zu ${ct(x.ct)} · spart ${eur(x.spart)}
                    <div class="muted">${x.grund ?? ""}</div>
                  </li>`,
              )}
            </ul>
            <div class="buttons">
              ${approve
                ? html`<button
                    class="action"
                    ?disabled=${this.busy}
                    @click=${() => this.call("script", "turn_on", { entity_id: approve })}
                  >
                    <ha-icon icon="mdi:check-circle"></ha-icon>Freigeben
                  </button>`
                : nothing}
              ${reject
                ? html`<button
                    class="action secondary"
                    ?disabled=${this.busy}
                    @click=${() => this.call("script", "turn_on", { entity_id: reject })}
                  >
                    <ha-icon icon="mdi:close-circle"></ha-icon>Ablehnen
                  </button>`
                : nothing}
            </div>`
        : mode.state === "Mit Freigabe"
          ? html`<div class="muted small">Nichts offen.</div>`
          : nothing}
    </div>`;
  }

  private renderChart(slots: SlotRow[]) {
    const legend = [
      ["Eigenverbrauch", ACTIONS.n.color],
      ["Entladung gesperrt", ACTIONS.h.color],
      ["Aus dem Netz laden", ACTIONS.l.color],
      ["1 kWh im Akku wert", COLORS.value],
      ["Akku mit Plan", COLORS.soc],
      ["Akku ohne Plan", COLORS.socWithout],
      ["PV", COLORS.pv],
      ["Verbrauch", COLORS.load],
      ["Wärmepumpen-Boost", COLORS.boost],
    ];
    return html`<style>
        .legend {
          display: flex;
          flex-wrap: wrap;
          gap: 4px 14px;
          margin-top: 4px;
        }
        .legend span {
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .legend i {
          width: 10px;
          height: 10px;
          border-radius: 3px;
          display: inline-block;
        }
      </style>
      <h2><ha-icon icon="mdi:chart-timeline-variant"></ha-icon>Plan</h2>
      ${slots.length
        ? html`<ems-chart .option=${this.option(slots)} .height=${490}></ems-chart>
            <div class="legend small muted">
              ${legend.map(([n, c]) => html`<span><i style="background:${c}"></i>${n}</span>`)}
            </div>
            <div class="muted small" style="margin-top:8px">
              Oben: Preis je Viertelstunde, gefärbt nach der geplanten Aktion; die grüne Linie ist, was 1 kWh im
              Akku später spart – liegt sie über dem Preis, lohnt sich Aufheben. Mitte: Akkustand mit und ohne
              Plan. Unten: PV-Prognose (AC) und erwarteter Verbrauch. Zum Zoomen ziehen oder scrollen.
            </div>`
        : html`<div class="muted">Kein Plan.</div>`}`;
  }

  private renderBlocks() {
    const plan = sensor(this.hass, this.config, "plan");
    const blocks: PlanBlock[] = plan?.attributes.aktionen ?? [];
    const boost: { von: number; bis: number }[] = plan?.attributes.wp_aktionen ?? [];
    const total = blocks.reduce((s, b) => s + (b.spart || 0), 0);
    const feedIn = Number(this.config.einstellungen.einspeisung_ct ?? 7);
    return html`<div class="card">
        <h2><ha-icon icon="mdi:format-list-checks"></ha-icon>Geplante Aktionen Akku</h2>
        ${blocks.length
          ? html`<div class="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Wann</th>
                    <th>Was</th>
                    <th class="num">Preis</th>
                    <th class="num">Spart</th>
                  </tr>
                </thead>
                <tbody>
                  ${blocks.map(
                    (b) =>
                      html`<tr>
                          <td style="white-space:nowrap">${when(b.von)}–${time(b.bis)}</td>
                          <td>
                            <span class="pill" style="background:${ACTIONS[b.was].color}"
                              >${ACTIONS[b.was].short}</span
                            >
                            ${b.was === "l" ? html` ${num(b.kwh, 1)} kWh` : nothing}
                          </td>
                          <td class="num">${ct(b.ct)}</td>
                          <td class="num">${eur(b.spart)}</td>
                        </tr>
                        <tr>
                          <td colspan="4" class="muted small" style="border-top:none;padding-top:0">
                            ${b.grund}${b.spart < MIN_SAVING ? " · unter 5 ct, wird nicht angefragt" : ""}
                          </td>
                        </tr>`,
                  )}
                  <tr>
                    <td></td>
                    <td><b>Zusammen</b></td>
                    <td></td>
                    <td class="num"><b>${eur(total)}</b></td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div class="muted small">nach Verlusten und Verschleiß</div>`
          : html`<div class="muted">Keine Eingriffe nötig. Der Akku läuft normal.</div>`}
      </div>
      <div class="card">
        <h2><ha-icon icon="mdi:heat-pump-outline"></ha-icon>Wärmepumpe</h2>
        ${boost.length
          ? html`<table>
              ${boost.map(
                (b) =>
                  html`<tr>
                    <td>${when(b.von)}–${time(b.bis)}</td>
                    <td><span class="pill" style="background:${COLORS.boost}">PV-Boost</span></td>
                  </tr>`,
              )}
            </table>`
          : html`<div class="muted">Kein PV-Boost geplant: nicht genug Überschuss, oder der Akku braucht ihn.</div>`}
        <p class="muted small">
          Boost nur mit PV-Überschuss, der sonst für ${num(feedIn, 0)} ct eingespeist würde – nie mit Netz- oder
          Akkustrom. Geplant wird nur, was nach dem Laden des Akkus übrig bleibt.
        </p>
      </div>`;
  }
}
define("ems-plan-view", EmsPlanView);
