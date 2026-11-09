import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { focusItem, nextIndex } from "../a11y";
import { define, type ChartOption } from "../chart";
import { ct, eur, num, time, toMs, when } from "../format";
import { available, configured, sensor } from "../ha";
import { QUARTER_MS, planOption } from "../options";
import { plainState, plainText } from "../texts";
import { ACTIONS, COLORS, NAME_TO_CODE, cardStyles, cssVar, pillColor } from "../theme";
import type { AppConfig, HassEntity, HomeAssistant, PlanBlock, SlotRow } from "../types";

const MODES: { name: string; text: string }[] = [
  { name: "Nur planen", text: "Der Plan wird nur angezeigt, am Akku wird nichts geschaltet." },
  { name: "Mit Freigabe", text: "Vor jedem Eingriff kommt eine Anfrage; geschaltet wird erst nach deiner Freigabe." },
  { name: "Automatisch", text: "Das EMS schaltet den Akku selbst nach Plan, ohne zu fragen." },
];
const MIN_SAVING = 0.05;
const FEEDBACK_MS = 120_000;

interface Feedback {
  kind: "ok" | "error";
  text: string;
  at: number;
}

/** What the plan does in a block, as a sentence for people who do not know the terms. */
export function blockSentence(b: PlanBlock): string {
  return b.was === "l"
    ? `Der Akku lädt ${num(b.kwh, 1)} kWh aus dem Netz`
    : "Der Akku hält seine Ladung und entlädt nicht";
}

/** One sentence that summarizes the plan chart for screen readers. */
export function chartSummary(slots: SlotRow[]): string {
  if (!slots.length) return "Kein Plan.";
  const hours = (code: string) => (slots.filter((s) => s[5] === code).length * QUARTER_MS) / 3_600_000;
  const soc = slots.map((s) => s[4]);
  const parts = [
    `Plan bis ${when(slots[slots.length - 1][0] + QUARTER_MS)}`,
    hours("h") ? `${num(hours("h"), 1)} Stunden Entladung gesperrt` : "",
    hours("l") ? `${num(hours("l"), 1)} Stunden Laden aus dem Netz` : "",
    `Akku zwischen ${Math.min(...soc)} und ${Math.max(...soc)} %`,
    `Preis zwischen ${ct(Math.min(...slots.map((s) => s[1])))} und ${ct(Math.max(...slots.map((s) => s[1])))}`,
  ];
  return `${parts.filter(Boolean).join(", ")}.`;
}

export class EmsPlanView extends LitElement {
  @property({ attribute: false }) hass!: HomeAssistant;
  @property({ attribute: false }) config!: AppConfig;
  @property({ type: Boolean }) narrow = false;
  @state() private busy = false;
  @state() private confirmMode?: string;
  @state() private feedback?: Feedback;

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

  private pending(): PlanBlock[] {
    const approval = configured(this.hass, this.config, "freigabe");
    return approval?.state === "on" ? (approval.attributes.aktionen ?? []) : [];
  }

  private approvedUntil(): number | null {
    const until = configured(this.hass, this.config, "freigabe_bis");
    const ms = until ? toMs(until.state.replace(" ", "T")) : null;
    return ms && ms > Date.now() ? ms : null;
  }

  private async call(domain: string, service: string, data: Record<string, unknown>, done: string) {
    this.busy = true;
    try {
      await this.hass.callService(domain, service, data);
      this.feedback = { kind: "ok", text: done, at: Date.now() };
    } catch (e) {
      const message = (e as { message?: string }).message ?? String(e);
      this.feedback = { kind: "error", text: `Hat nicht geklappt: ${message}. Bitte noch einmal versuchen.`, at: Date.now() };
    } finally {
      this.busy = false;
    }
  }

  render() {
    const plan = sensor(this.hass, this.config, "plan");
    const pending = this.pending();
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
          align-items: start;
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
        ${pending.length ? this.renderApproval(pending) : nothing}
        <div class="row ${control === nothing ? "single" : ""}">${this.renderNow()} ${control}</div>
        <div class="card">${this.renderChart(plan?.attributes.slots ?? [])}</div>
        <div class="row">${this.renderBlocks()}</div>
      </div>`;
  }

  // ---- 1. Is something waiting for me? ----

  private renderApproval(pending: PlanBlock[]) {
    const approve = this.config.entitaeten.freigeben;
    const reject = this.config.entitaeten.ablehnen;
    const color = ACTIONS[pending[0].was]?.color ?? ACTIONS.h.color;
    return html`<section class="card approval" style="--tone:${color}" aria-labelledby="approval-title">
      <style>
        .approval {
          background: color-mix(in srgb, var(--tone) 9%, var(--ha-card-background, var(--card-background-color, #fff)));
          border-color: color-mix(in srgb, var(--tone) 45%, transparent);
        }
        .approval h2 {
          font-size: 1.25rem;
        }
        .approval ul {
          list-style: none;
          margin: 0 0 16px;
          padding: 0;
          display: grid;
          gap: 14px;
        }
        .approval li {
          display: grid;
          grid-template-columns: auto 1fr;
          gap: 4px 12px;
        }
        .approval .icon {
          grid-row: span 3;
          width: 40px;
          height: 40px;
          border-radius: 12px;
          display: grid;
          place-items: center;
          color: #fff;
        }
        .approval .what {
          font-size: 1.05rem;
          font-weight: 600;
          line-height: 1.35;
        }
        .approval .cost {
          font-variant-numeric: tabular-nums;
        }
        .approval .cost b {
          white-space: nowrap;
        }
        .approval .why {
          color: var(--secondary-text-color);
          font-size: 0.9rem;
          line-height: 1.45;
          max-width: 70ch;
        }
        .buttons {
          display: flex;
          gap: 10px;
          flex-wrap: wrap;
        }
        .buttons button {
          flex: 1 1 160px;
          max-width: 260px;
        }
      </style>
      <h2 id="approval-title">
        <ha-icon icon="mdi:account-question-outline"></ha-icon>
        ${pending.length === 1 ? "Deine Freigabe ist gefragt" : `${pending.length} Aktionen warten auf deine Freigabe`}
      </h2>
      <ul>
        ${pending.map(
          (b) =>
            html`<li>
              <span class="icon" style="background:${ACTIONS[b.was]?.color}" aria-hidden="true"
                ><ha-icon icon=${ACTIONS[b.was]?.icon ?? "mdi:battery"}></ha-icon
              ></span>
              <span class="what">${when(b.von)}–${time(b.bis)}: ${blockSentence(b)}</span>
              <span class="cost">Strom kostet dann ${ct(b.ct)} · spart ca. <b>${eur(b.spart)}</b></span>
              ${b.grund ? html`<span class="why">${plainText(b.grund)}</span>` : nothing}
            </li>`,
        )}
      </ul>
      <div class="buttons">
        ${approve
          ? html`<button
              class="action"
              ?disabled=${this.busy}
              @click=${() =>
                this.call(
                  "script",
                  "turn_on",
                  { entity_id: approve },
                  pending.length === 1
                    ? "Freigegeben. Das EMS schaltet zur geplanten Zeit."
                    : `${pending.length} Aktionen freigegeben. Das EMS schaltet zur geplanten Zeit.`,
                )}
            >
              <ha-icon icon="mdi:check"></ha-icon>${pending.length === 1 ? "Freigeben" : "Alle freigeben"}
            </button>`
          : nothing}
        ${reject
          ? html`<button
              class="action secondary"
              ?disabled=${this.busy}
              @click=${() =>
                this.call("script", "turn_on", { entity_id: reject }, "Abgelehnt. Der Akku läuft normal weiter.")}
            >
              <ha-icon icon="mdi:close"></ha-icon>Ablehnen
            </button>`
          : nothing}
      </div>
    </section>`;
  }

  // ---- 2. What does the battery do, and why? ----

  private renderNow() {
    const plan = sensor(this.hass, this.config, "plan");
    const value = sensor(this.hass, this.config, "wert");
    const soc = configured(this.hass, this.config, "soc");
    if (!available(plan)) {
      return html`<section class="card">
        <h2><ha-icon icon="mdi:battery-alert"></ha-icon>Planer nicht verfügbar</h2>
        <div class="warn error">
          Es fehlen Preise oder der Akkustand. Solange es keinen Plan gibt, läuft der Akku normal (Eigenverbrauch).
          Welche Eingabe fehlt, steht unter Protokoll → Diagnose.
        </div>
      </section>`;
    }
    const a = plan.attributes;
    const code = NAME_TO_CODE[plan.state] ?? "n";
    const action = ACTIONS[code];
    const computed = toMs(a.berechnet);
    const blocks: PlanBlock[] = a.aktionen ?? [];
    const next = blocks.find((b) => b.bis > Date.now() && !(b.von <= Date.now() && code === b.was));
    const charge = Number(a.netzladen_kwh) || 0;
    return html`<section class="card now" aria-labelledby="now-title">
      <style>
        .now .head {
          display: flex;
          gap: 14px;
          align-items: center;
          margin-bottom: 10px;
        }
        .now .badge {
          width: 48px;
          height: 48px;
          border-radius: 14px;
          display: grid;
          place-items: center;
          flex: none;
          color: #fff;
        }
        .now .badge ha-icon {
          --mdc-icon-size: 28px;
        }
        .now h2 {
          font-size: 1.45rem;
          margin: 0;
          line-height: 1.25;
        }
        .now .reason {
          margin: 0 0 16px;
          line-height: 1.5;
          max-width: 72ch;
        }
        .now dl {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 12px 16px;
          margin: 0 0 14px;
        }
        .now dt {
          color: var(--secondary-text-color);
          font-size: 0.875rem;
        }
        .now dd {
          margin: 2px 0 0;
          font-size: 1.15rem;
          font-weight: 600;
          font-variant-numeric: tabular-nums;
        }
        .now .next {
          display: flex;
          gap: 8px;
          align-items: baseline;
          flex-wrap: wrap;
          padding: 10px 0 0;
          border-top: 1px solid var(--divider-color);
        }
      </style>
      <div class="head">
        <div class="badge" style="background:${action.color}" aria-hidden="true">
          <ha-icon icon=${action.icon}></ha-icon>
        </div>
        <div>
          <h2 id="now-title">${action.sentence}</h2>
          <div class="muted small">${action.name} · berechnet ${computed ? time(computed) : "–"}</div>
        </div>
      </div>
      ${a.grund && plainText(a.grund).replace(/^Der /, "") !== action.sentence.replace(/^Der /, "")
        ? html`<p class="reason">${plainText(a.grund)}</p>`
        : html`<p class="reason muted">Im Moment lohnt sich kein Eingriff. Der Akku deckt den Verbrauch, solange er Ladung hat.</p>`}
      <dl>
        <div>
          <dt>Akku</dt>
          <dd>${available(soc) ? `${Math.round(Number(soc.state))} %` : "–"}</dd>
        </div>
        <div>
          <dt>Strom jetzt</dt>
          <dd>${ct(a.slots?.[0]?.[1])}</dd>
        </div>
        <div>
          <dt>1 kWh im Akku ist wert</dt>
          <dd style="color:${COLORS.value}">${available(value) ? ct(value.state) : "–"}</dd>
        </div>
        <div>
          <dt>Plan spart bis ${a.bis ? when(toMs(a.bis)!) : "–"}</dt>
          <dd>${eur(a.ersparnis_eur)}</dd>
        </div>
      </dl>
      <div class="next small">
        ${next
          ? html`<b>Als Nächstes:</b>
              <span>${when(next.von)}–${time(next.bis)}</span>
              <span class="pill" style=${pillColor(next.was)}>${ACTIONS[next.was].name}</span>
              <span class="muted">spart ${eur(next.spart)}${charge > 0 && next.was === "l" ? ` · ${num(charge, 1)} kWh aus dem Netz` : ""}</span>`
          : html`<span class="muted">Keine weiteren Eingriffe geplant. Der Akku läuft normal.</span>`}
      </div>
    </section>`;
  }

  // ---- 3. Control ----

  private selectMode(mode: HassEntity, option: string) {
    if (option === mode.state) return;
    const from = MODES.findIndex((m) => m.name === mode.state);
    const to = MODES.findIndex((m) => m.name === option);
    // switching to a mode that acts more on its own asks first; back to a safer mode does not
    if (to > from) {
      this.confirmMode = option;
      return;
    }
    this.applyMode(mode, option);
  }

  private applyMode(mode: HassEntity, option: string) {
    this.confirmMode = undefined;
    this.call(
      mode.entity_id.split(".")[0],
      "select_option",
      { entity_id: mode.entity_id, option },
      `Modus „${option}“ ist eingestellt.`,
    );
  }

  private onModeKey(ev: KeyboardEvent, mode: HassEntity, options: string[]) {
    const current = Math.max(0, options.indexOf(mode.state));
    const index = nextIndex(ev.key, current, options.length);
    if (index == null) return;
    ev.preventDefault();
    focusItem(this.renderRoot, ".mode", index);
  }

  private renderControl() {
    const mode = configured(this.hass, this.config, "modus");
    if (!mode) return nothing;
    const target = configured(this.hass, this.config, "soll");
    const inverter = configured(this.hass, this.config, "ems_modus");
    const until = this.approvedUntil();
    const untilEntity = this.config.entitaeten.freigabe_bis;
    const ist: string | undefined = target?.attributes.ist;
    const options: string[] = mode.attributes.options ?? MODES.map((m) => m.name);
    const feedback = this.feedback && Date.now() - this.feedback.at < FEEDBACK_MS ? this.feedback : undefined;
    const plan = sensor(this.hass, this.config, "plan");
    const next = ((plan?.attributes.aktionen ?? []) as PlanBlock[]).find((b) => b.bis > Date.now());
    return html`<section class="card control" aria-labelledby="control-title">
      <style>
        .modes {
          display: grid;
          gap: 6px;
          margin-bottom: 12px;
        }
        .mode {
          font: inherit;
          text-align: left;
          display: grid;
          grid-template-columns: auto 1fr;
          gap: 2px 10px;
          align-items: start;
          padding: 10px 12px;
          min-height: 44px;
          border-radius: 10px;
          border: 1px solid var(--divider-color);
          background: transparent;
          color: var(--primary-text-color);
          cursor: pointer;
        }
        .mode .dot {
          grid-row: span 2;
          width: 18px;
          height: 18px;
          margin-top: 1px;
          border-radius: 50%;
          border: 2px solid var(--secondary-text-color);
          box-sizing: border-box;
        }
        .mode[aria-checked="true"] {
          border-color: var(--primary-color);
          background: color-mix(in srgb, var(--primary-color) 10%, transparent);
        }
        .mode[aria-checked="true"] .dot {
          border: 5px solid var(--primary-color);
        }
        .mode .name {
          font-weight: 600;
        }
        .mode .text {
          color: var(--secondary-text-color);
          font-size: 0.85rem;
          line-height: 1.35;
        }
        .confirm {
          border-radius: 10px;
          padding: 12px;
          margin-bottom: 12px;
          background: color-mix(in srgb, var(--warning-color, #ffa600) 14%, transparent);
          line-height: 1.45;
        }
        .confirm .buttons {
          display: flex;
          gap: 8px;
          flex-wrap: wrap;
          margin-top: 10px;
        }
        .facts {
          display: grid;
          grid-template-columns: auto 1fr;
          gap: 6px 12px;
          margin: 0;
        }
        .facts dt {
          color: var(--secondary-text-color);
        }
        .facts dd {
          margin: 0;
        }
        .status {
          border-radius: 10px;
          padding: 10px 12px;
          margin-bottom: 12px;
          background: color-mix(in srgb, var(--success-color, #43a047) 14%, transparent);
        }
        .status.error {
          background: color-mix(in srgb, var(--error-color, #db4437) 14%, transparent);
        }
        .link {
          font: inherit;
          background: none;
          border: none;
          padding: 4px 0;
          min-height: 32px;
          color: var(--primary-color);
          text-decoration: underline;
          text-underline-offset: 2px;
          cursor: pointer;
        }
      </style>
      <h2 id="control-title"><ha-icon icon="mdi:tune-variant"></ha-icon>Steuerung</h2>
      <div role="status" aria-live="polite">
        ${feedback ? html`<div class="status ${feedback.kind === "error" ? "error" : ""}">${feedback.text}</div>` : nothing}
      </div>
      <div class="modes" role="radiogroup" aria-label="Modus der Steuerung">
        ${options.map((o) => {
          const m = MODES.find((x) => x.name === o);
          const checked = o === mode.state;
          return html`<button
            class="mode"
            role="radio"
            aria-checked=${checked}
            tabindex=${checked ? 0 : -1}
            ?disabled=${this.busy}
            @click=${() => this.selectMode(mode, o)}
            @keydown=${(ev: KeyboardEvent) => this.onModeKey(ev, mode, options)}
          >
            <span class="dot" aria-hidden="true"></span><span class="name">${o}</span>
            ${m ? html`<span class="text">${m.text}</span>` : nothing}
          </button>`;
        })}
      </div>
      ${this.confirmMode
        ? html`<div class="confirm" role="alertdialog" aria-label="Modus wechseln">
            <b>„${this.confirmMode}“ einschalten?</b>
            ${MODES.find((m) => m.name === this.confirmMode)?.text}
            ${next
              ? html`<div>Als Nächstes: ${when(next.von)}–${time(next.bis)} ${ACTIONS[next.was].name}.</div>`
              : html`<div>Im Moment ist kein Eingriff geplant.</div>`}
            <div class="buttons">
              <button class="action" ?disabled=${this.busy} @click=${() => this.applyMode(mode, this.confirmMode!)}>
                Einschalten
              </button>
              <button class="action secondary" @click=${() => (this.confirmMode = undefined)}>Abbrechen</button>
            </div>
          </div>`
        : nothing}
      <dl class="facts small">
        ${target ? html`<dt>EMS will</dt><dd>${plainState(target.state)}</dd>` : nothing}
        ${inverter ? html`<dt>Wechselrichter</dt><dd>${plainState(inverter.state)}</dd>` : nothing}
        ${until
          ? html`<dt>Freigegeben bis</dt>
              <dd>
                ${when(until)}
                ${untilEntity?.startsWith("input_datetime.")
                  ? html`<br /><button
                        class="link"
                        ?disabled=${this.busy}
                        @click=${() =>
                          this.call(
                            "input_datetime",
                            "set_datetime",
                            { entity_id: untilEntity, timestamp: Math.floor(Date.now() / 1000) },
                            "Freigabe zurückgenommen. Der Akku läuft normal weiter.",
                          )}
                      >
                        Freigabe zurücknehmen
                      </button>`
                  : nothing}
              </dd>`
          : nothing}
      </dl>
      ${ist && ist.startsWith("Anders")
        ? html`<div class="warn">
            Am Wechselrichter ist etwas anderes eingestellt (${plainText(ist)}). Das EMS greift nicht ein, bis das
            wieder zurückgestellt ist.
          </div>`
        : nothing}
      ${target && target.attributes.plan_aktuell === false
        ? html`<div class="warn error">Der Plan ist veraltet. Bis zum nächsten Plan läuft der Akku normal.</div>`
        : nothing}
    </section>`;
  }

  // ---- Chart and lists ----

  private renderChart(slots: SlotRow[]) {
    const legend: [string, string, string?][] = [
      ["Eigenverbrauch", ACTIONS.n.color],
      ["Entladung gesperrt", ACTIONS.h.color],
      ["Laden aus dem Netz", ACTIONS.l.color],
      ["1 kWh im Akku wert", COLORS.value, "line"],
      ["Akku mit Plan", COLORS.soc, "line"],
      ["Akku ohne Plan", COLORS.socWithout, "dash"],
      ["PV", COLORS.pv],
      ["Verbrauch", COLORS.load, "line"],
      ["PV-Boost", COLORS.boost],
      ["Jetzt", cssVar(this, "--primary-color", "#03a9f4"), "dash"],
    ];
    return html`<style>
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
          display: inline-block;
        }
        .legend i.line {
          height: 3px;
          border-radius: 2px;
        }
        .legend i.dash {
          height: 0;
          border-top: 2px dashed;
          background: none !important;
        }
        .explain {
          margin: 10px 0 0;
          line-height: 1.5;
          max-width: 80ch;
        }
      </style>
      <h2><ha-icon icon="mdi:chart-timeline-variant"></ha-icon>Plan</h2>
      ${slots.length
        ? html`<p class="explain" style="margin:0 0 8px">
              <b>So liest du das:</b> Liegt die grüne Linie (was 1 kWh im Akku später spart) über dem Preis, lohnt es sich,
              den Akku für später aufzuheben.
            </p>
            <ems-chart .option=${this.option(slots)} .height=${470} .label=${chartSummary(slots)}></ems-chart>
            <ul class="legend small muted" aria-label="Legende">
              ${legend.map(
                ([n, c, kind]) =>
                  html`<li><i class=${kind ?? ""} style="background:${c};border-color:${c}"></i>${n}</li>`,
              )}
            </ul>
            <p class="explain small muted">
              Oben: Preis je Viertelstunde, gefärbt nach der geplanten Aktion. Mitte: Akkustand mit und ohne Plan.
              Unten: PV-Prognose und erwarteter Verbrauch. Zum Zoomen ziehen oder scrollen, antippen für Details.
            </p>`
        : html`<div class="muted">Kein Plan.</div>`}`;
  }

  private renderBlocks() {
    const plan = sensor(this.hass, this.config, "plan");
    const hp = sensor(this.hass, this.config, "waermepumpe");
    const blocks: PlanBlock[] = plan?.attributes.aktionen ?? [];
    const boost: { von: number; bis: number }[] = plan?.attributes.wp_aktionen ?? [];
    const total = blocks.reduce((s, b) => s + (b.spart || 0), 0);
    const feedIn = Number(this.config.einstellungen.einspeisung_ct ?? 7);
    return html`<section class="card" aria-labelledby="blocks-title">
        <style>
          .blocks {
            list-style: none;
            margin: 0;
            padding: 0;
          }
          .blocks li {
            padding: 10px 0;
            border-bottom: 1px solid var(--divider-color);
            display: grid;
            gap: 4px;
          }
          .blocks .top {
            display: flex;
            flex-wrap: wrap;
            gap: 6px 10px;
            align-items: center;
          }
          .blocks .top .save {
            margin-left: auto;
            white-space: nowrap;
            font-weight: 600;
            font-variant-numeric: tabular-nums;
          }
          .total {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            padding: 10px 0 0;
            font-weight: 600;
          }
          .total span:last-child {
            white-space: nowrap;
          }
        </style>
        <h2 id="blocks-title"><ha-icon icon="mdi:format-list-checks"></ha-icon>Geplante Aktionen</h2>
        ${blocks.length
          ? html`<ul class="blocks">
                ${blocks.map(
                  (b) =>
                    html`<li>
                      <div class="top">
                        <span class="num-tab">${when(b.von)}–${time(b.bis)}</span>
                        <span class="pill" style=${pillColor(b.was)}>${ACTIONS[b.was].name}</span>
                        <span class="muted small"
                          >${b.was === "l" ? `${num(b.kwh, 1)} kWh · ` : ""}Strom ${ct(b.ct)}</span
                        >
                        <span class="save">${eur(b.spart)}</span>
                      </div>
                      <div class="muted small">
                        ${plainText(b.grund)}${b.spart < MIN_SAVING
                          ? " · Spart weniger als 5 ct, deshalb wird nicht gefragt."
                          : ""}
                      </div>
                    </li>`,
                )}
              </ul>
              <div class="total"><span>Zusammen, nach Verlusten und Verschleiß</span><span>${eur(total)}</span></div>`
          : html`<p class="muted">Keine Eingriffe nötig. Der Akku läuft normal.</p>`}
      </section>
      <section class="card" aria-labelledby="hp-title">
        <h2 id="hp-title"><ha-icon icon="mdi:heat-pump-outline"></ha-icon>Wärmepumpe</h2>
        <p style="margin:0 0 10px">
          ${available(hp) && hp.state === "PV-Boost"
            ? html`Läuft gerade im <b>PV-Boost</b>.`
            : html`Läuft gerade im Normalbetrieb.`}
        </p>
        ${boost.length
          ? html`<ul class="blocks">
              ${boost.map(
                (b) =>
                  html`<li>
                    <div class="top">
                      <span class="num-tab">${when(b.von)}–${time(b.bis)}</span>
                      <span class="pill" style="--pill:${COLORS.boost}">PV-Boost</span>
                    </div>
                  </li>`,
              )}
            </ul>`
          : html`<p class="muted" style="margin:0">
              Kein PV-Boost geplant: nicht genug Überschuss, oder der Akku braucht ihn.
            </p>`}
        <p class="muted small">
          PV-Boost heißt: Die Wärmepumpe heizt etwas mehr vor, nur mit PV-Überschuss, der sonst für
          ${num(feedIn, 0)} ct eingespeist würde – nie mit Netz- oder Akkustrom.
        </p>
      </section>`;
  }
}
define("ems-plan-view", EmsPlanView);
