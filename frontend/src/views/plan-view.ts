import { LitElement, html, nothing } from "lit";
import { property, state } from "lit/decorators.js";
import { focusItem, nextIndex } from "../a11y";
import { define, type ChartOption } from "../chart";
import { ct, eur, num, time, toMs, when } from "../format";
import { available, configured, sensor } from "../ha";
import { QUARTER_MS, planOption, priceLevel } from "../options";
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
  /** Where the answer appears: next to the approval card or in the control card. */
  place: "approval" | "control";
}

/** What the plan does in a block, as a sentence for people who do not know the terms. */
export function blockSentence(b: PlanBlock): string {
  return b.was === "l"
    ? `Der Akku lädt ${num(b.kwh, 1)} kWh aus dem Netz`
    : "Der Akku hält seine Ladung und entlädt nicht";
}

/** How long the battery lasts by the plan: until a time, or empty now and charging again from a time,
 * or beyond the end of the plan. Empty = at or below 1 % of the usable range. */
export function batteryLasts(slots: SlotRow[], level: number | null): { label: string; value: string } {
  if (!slots.length) return { label: "Akku reicht", value: "–" };
  const EMPTY = 1;
  if (level != null && level <= EMPTY) {
    const refill = slots.find((s) => s[4] > EMPTY);
    return refill
      ? { label: "Akku ist leer, lädt wieder ab", value: when(refill[0]) }
      : { label: "Akku ist leer", value: "im ganzen Plan" };
  }
  const out = slots.find((s) => s[4] <= EMPTY);
  if (!out) return { label: "Akku reicht", value: "bis Planende" };
  return { label: "Akku reicht bis", value: when(out[0]) };
}

/** Lowest planned state of charge and when it is reached. */
export function lowPoint(slots: SlotRow[]): { soc: number; at: number } | null {
  if (!slots.length) return null;
  const s = slots.reduce((lowest, x) => (x[4] < lowest[4] ? x : lowest));
  return { soc: s[4], at: s[0] + QUARTER_MS };
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
  private chartCompact?: boolean;
  private chartOption?: ChartOption;

  static styles = [cardStyles];

  private option(slots: SlotRow[]): ChartOption {
    // recompute only when the plan changed (hass changes on every state change in the house)
    const compact = this.narrow || this.offsetWidth < 600;
    if (this.chartFor !== slots || this.chartCompact !== compact) {
      this.chartFor = slots;
      this.chartCompact = compact;
      this.chartOption = planOption(
        slots,
        Date.now(),
        cssVar(this, "--primary-color", "#03a9f4"),
        cssVar(this, "--primary-text-color", "#212121"),
        compact,
      );
    }
    return this.chartOption!;
  }

  private pending(): PlanBlock[] {
    const approval = configured(this.hass, this.config, "freigabe");
    return approval?.state === "on" ? (approval.attributes.aktionen ?? []) : [];
  }

  private approvedUntil(): number | null {
    const approval = configured(this.hass, this.config, "freigabe");
    const ms = toMs(approval?.attributes.freigegeben_bis);
    return ms && ms > Date.now() ? ms : null;
  }

  /** Plan blocks an existing approval covers that have not ended yet. */
  private approvedBlocks(until: number): PlanBlock[] {
    const approval = configured(this.hass, this.config, "freigabe");
    const codes: string[] = approval?.attributes.freigegeben ?? [];
    const plan = sensor(this.hass, this.config, "plan");
    const blocks: PlanBlock[] = plan?.attributes.aktionen ?? [];
    return blocks.filter((b) => codes.includes(b.was) && b.bis > Date.now() && b.von < until);
  }

  private visibleFeedback(place: Feedback["place"]): Feedback | undefined {
    const f = this.feedback;
    if (!f || Date.now() - f.at >= FEEDBACK_MS) return undefined;
    // without a control card, every answer appears at the top
    const hasControl = !!configured(this.hass, this.config, "modus");
    return f.place === place || (!hasControl && place === "approval") ? f : undefined;
  }

  private async call(
    domain: string,
    service: string,
    data: Record<string, unknown>,
    done: string,
    place: Feedback["place"] = "control",
  ) {
    this.busy = true;
    try {
      await this.hass.callService(domain, service, data);
      this.feedback = { kind: "ok", text: done, at: Date.now(), place };
    } catch (e) {
      const message = (e as { message?: string }).message ?? String(e);
      this.feedback = {
        kind: "error",
        text: `Hat nicht geklappt: ${plainText(message)}. Bitte noch einmal versuchen.`,
        at: Date.now(),
        place,
      };
    } finally {
      this.busy = false;
    }
  }

  render() {
    const plan = sensor(this.hass, this.config, "plan");
    const pending = this.pending();
    const until = this.approvedUntil();
    const approved = !pending.length && until ? this.approvedBlocks(until) : [];
    const control = this.renderControl();
    const topFeedback = this.visibleFeedback("approval");
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
        .top-status:not(:has(.warn)) {
          display: none;
        }
        .top-status .warn {
          margin: 0;
          font-size: 1rem;
        }
        @media (max-width: 900px) {
          .row {
            grid-template-columns: minmax(0, 1fr);
          }
        }
      </style>
      <div class="stack">
        <div role="status" aria-live="polite" class="top-status">
          ${topFeedback
            ? html`<div class="warn ${topFeedback.kind === "error" ? "error" : "ok"}">${topFeedback.text}</div>`
            : nothing}
        </div>
        ${pending.length ? this.renderApproval(pending) : approved.length ? this.renderApproved(approved, until!) : nothing}
        <div class="row ${control === nothing ? "single" : ""}">${this.renderNow()} ${control}</div>
        <div class="card">${this.renderChart(plan?.attributes.slots ?? [])}</div>
        <div class="row">${this.renderBlocks()}</div>
      </div>`;
  }

  // ---- 1. Is something waiting for me? ----

  private renderApproval(pending: PlanBlock[]) {
    const color = ACTIONS[pending[0].was]?.color ?? ACTIONS.h.color;
    const approval = configured(this.hass, this.config, "freigabe");
    const holds = toMs(approval?.attributes.gilt_bis_bei_freigabe);
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
          grid-row: span 4;
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
        .approval details {
          font-size: 0.9rem;
        }
        .approval details summary {
          min-height: 44px;
          display: flex;
          align-items: center;
          list-style: none;
          gap: 4px;
          width: fit-content;
        }
        .approval details summary::-webkit-details-marker {
          display: none;
        }
        .approval details summary::before {
          content: "▸";
          transition: transform 0.15s ease-out;
        }
        .approval details[open] summary::before {
          transform: rotate(90deg);
        }
        .approval .scope {
          margin: 0 0 14px;
          max-width: 70ch;
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
              <span class="cost">Spart ca. <b>${eur(b.spart)}</b></span>
              ${b.grund ? html`<span class="why">${plainText(b.grund)}.</span>` : nothing}
              ${b.rechnung
                ? html`<details class="why">
                    <summary>Rechnung</summary>
                    ${plainText(b.rechnung)}.
                  </details>`
                : nothing}
            </li>`,
        )}
      </ul>
      <p class="scope">
        ${holds ? html`Die Freigabe gilt bis <b>${when(holds)}</b>. ` : nothing}Ohne Freigabe läuft der Akku normal
        weiter.
      </p>
      <div class="buttons">
        <button
          class="action"
          ?disabled=${this.busy}
          @click=${() =>
            this.call(
              "ems",
              "freigeben",
              {},
              pending.length === 1
                ? "Freigegeben. Das EMS schaltet zur geplanten Zeit."
                : `${pending.length} Aktionen freigegeben. Das EMS schaltet zur geplanten Zeit.`,
              "approval",
            )}
        >
          <ha-icon icon="mdi:check"></ha-icon>${pending.length === 1 ? "Freigeben" : "Alle freigeben"}
        </button>
        <button
          class="action secondary"
          ?disabled=${this.busy}
          @click=${() =>
            this.call("ems", "ablehnen", {}, "Abgelehnt. Der Akku läuft normal weiter.", "approval")}
        >
          <ha-icon icon="mdi:close"></ha-icon>Ablehnen
        </button>
      </div>
    </section>`;
  }

  /** After the approval: what runs when, how long the approval holds, and the way back. */
  private renderApproved(blocks: PlanBlock[], until: number) {
    const color = ACTIONS[blocks[0].was]?.color ?? ACTIONS.h.color;
    return html`<section class="card approved" style="--tone:${color}" aria-labelledby="approved-title">
      <style>
        .approved {
          background: color-mix(in srgb, var(--tone) 9%, var(--ha-card-background, var(--card-background-color, #fff)));
          border-color: color-mix(in srgb, var(--tone) 45%, transparent);
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 12px 24px;
          padding: 16px 20px;
        }
        .approved .body {
          flex: 1 1 320px;
          min-width: 0;
        }
        .approved h2 {
          font-size: 1.1rem;
          margin: 0 0 4px;
        }
        .approved ul {
          list-style: none;
          margin: 0 0 4px;
          padding: 0;
          display: grid;
          gap: 4px;
        }
        .approved li {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 4px 10px;
          font-variant-numeric: tabular-nums;
        }
      </style>
      <div class="body">
        <h2 id="approved-title"><ha-icon icon="mdi:check-circle-outline"></ha-icon>Freigegeben</h2>
        <ul>
          ${blocks.map(
            (b) =>
              html`<li>
                <span>${when(b.von)}–${time(b.bis)}</span>
                <span class="pill" style=${pillColor(b.was)}>${ACTIONS[b.was].name}</span>
                <span class="muted">spart ca. ${eur(b.spart)}</span>
              </li>`,
          )}
        </ul>
        <div class="muted small">Gilt bis ${when(until)}. Das EMS schaltet zur geplanten Zeit.</div>
      </div>
      <button
        class="action secondary"
        ?disabled=${this.busy}
        @click=${() =>
          this.call(
            "ems",
            "freigabe_zuruecknehmen",
            {},
            "Freigabe zurückgenommen. Der Akku läuft normal weiter.",
            "approval",
          )}
      >
        <ha-icon icon="mdi:undo"></ha-icon>Freigabe zurücknehmen
      </button>
    </section>`;
  }

  // ---- 2. What does the battery do, and why? ----

  private renderNow() {
    const plan = sensor(this.hass, this.config, "plan");
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
    const slots: SlotRow[] = a.slots ?? [];
    const code = NAME_TO_CODE[plan.state] ?? "n";
    const action = ACTIONS[code];
    const computed = toMs(a.berechnet);
    const blocks: PlanBlock[] = a.aktionen ?? [];
    const next = blocks.find((b) => b.bis > Date.now() && !(b.von <= Date.now() && code === b.was));
    const charge = Number(a.netzladen_kwh) || 0;
    const level = available(soc) ? Math.round(Number(soc.state)) : null;
    const low = lowPoint(slots);
    const lasts = batteryLasts(slots, level);
    const prices = slots.map((s) => s[1]);
    const price = prices[0];
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    return html`<section class="card now" aria-labelledby="now-title">
      <style>
        .now {
          padding: 20px;
        }
        .now .head {
          display: flex;
          gap: 16px;
          align-items: center;
          margin-bottom: 12px;
        }
        .now .badge {
          width: 60px;
          height: 60px;
          border-radius: 18px;
          display: grid;
          place-items: center;
          flex: none;
          color: #fff;
        }
        .now .badge ha-icon {
          --mdc-icon-size: 34px;
        }
        .now h2 {
          font-size: 1.9rem;
          margin: 0;
          line-height: 1.2;
          letter-spacing: -0.015em;
          text-wrap: balance;
        }
        .now .reason {
          margin: 0 0 20px;
          font-size: 1.05rem;
          line-height: 1.5;
          max-width: 72ch;
          text-wrap: pretty;
        }
        .gauges {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr));
          gap: 20px 28px;
          margin-bottom: 20px;
        }
        .gauge .label {
          color: var(--secondary-text-color);
          font-size: 0.95rem;
        }
        .gauge .big {
          font-size: 2.6rem;
          font-weight: 600;
          line-height: 1.1;
          letter-spacing: -0.02em;
          font-variant-numeric: tabular-nums;
        }
        .gauge .big small {
          font-size: 1.1rem;
          font-weight: 500;
          letter-spacing: 0;
          margin-left: 4px;
        }
        .gauge .note {
          color: var(--secondary-text-color);
          font-size: 0.95rem;
          margin-top: 6px;
        }
        .bar {
          position: relative;
          height: 12px;
          border-radius: 6px;
          margin-top: 10px;
          background: color-mix(in srgb, var(--primary-text-color) 10%, transparent);
        }
        .bar .fill {
          position: absolute;
          inset: 0 auto 0 0;
          border-radius: 6px;
          background: ${COLORS.soc};
        }
        .bar .mark {
          position: absolute;
          top: -4px;
          bottom: -4px;
          width: 3px;
          margin-left: -1.5px;
          border-radius: 2px;
          background: var(--primary-text-color);
        }
        .scale {
          position: relative;
          height: 12px;
          border-radius: 6px;
          margin-top: 10px;
          background: linear-gradient(90deg, ${COLORS.cheap}, ${COLORS.middle}, ${COLORS.expensive});
        }
        .scale .mark {
          position: absolute;
          top: -4px;
          bottom: -4px;
          width: 4px;
          margin-left: -2px;
          border-radius: 2px;
          background: var(--primary-text-color);
          outline: 2px solid var(--ha-card-background, var(--card-background-color, #fff));
        }
        .now dl {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 12px 28px;
          margin: 0 0 16px;
        }
        .now dt {
          color: var(--secondary-text-color);
          font-size: 0.95rem;
        }
        .now dd {
          margin: 2px 0 0;
          font-size: 1.35rem;
          font-weight: 600;
          font-variant-numeric: tabular-nums;
        }
        .now .next {
          display: flex;
          gap: 8px 10px;
          align-items: center;
          flex-wrap: wrap;
          padding: 14px 0 0;
          border-top: 1px solid var(--divider-color);
        }
        @media (max-width: 600px) {
          .now h2 {
            font-size: 1.55rem;
          }
          .gauge .big {
            font-size: 2.2rem;
          }
        }
      </style>
      <div class="head">
        <div class="badge" style="background:${action.color}" aria-hidden="true">
          <ha-icon icon=${action.icon}></ha-icon>
        </div>
        <div>
          <h2 id="now-title">${action.sentence}</h2>
          <div class="muted">${action.name} · berechnet ${computed ? time(computed) : "–"}</div>
        </div>
      </div>
      ${a.grund && plainText(a.grund).replace(/^Der /, "") !== action.sentence.replace(/^Der /, "")
        ? html`<p class="reason">${plainText(a.grund)}</p>`
        : html`<p class="reason muted">
            Im Moment lohnt sich kein Eingriff. Der Akku deckt den Verbrauch, solange er Ladung hat.
          </p>`}
      <div class="gauges">
        <div class="gauge">
          <div class="label">Akku</div>
          <div class="big">${level ?? "–"}<small>%</small></div>
          <div class="bar" role="img" aria-label="Akkustand ${level ?? "unbekannt"} Prozent">
            <div class="fill" style="width:${level ?? 0}%"></div>
            ${low ? html`<div class="mark" style="left:${low.soc}%"></div>` : nothing}
          </div>
          <div class="note">
            ${low && (level ?? 0) > 1 ? html`Tiefster Stand laut Plan: <b>${low.soc} %</b> ${when(low.at)}` : nothing}
          </div>
        </div>
        <div class="gauge">
          <div class="label">Strom jetzt</div>
          <div class="big">${price != null ? Math.round(price) : "–"}<small>ct/kWh</small></div>
          ${price != null
            ? html`<div
                  class="scale"
                  role="img"
                  aria-label="Der Preis ist ${priceLevel(price, min, max)}, zwischen ${ct(min)} und ${ct(max)} im Plan"
                >
                  <div class="mark" style="left:${max > min ? ((price - min) / (max - min)) * 100 : 50}%"></div>
                </div>
                <div class="note">
                  Gerade <b>${priceLevel(price, min, max)}</b> · im Plan zwischen ${ct(min)} und ${ct(max)}
                </div>`
            : nothing}
        </div>
      </div>
      <dl>
        <div>
          <dt>${lasts.label}</dt>
          <dd>${lasts.value}</dd>
        </div>
        <div>
          <dt>Plan spart bis ${a.bis ? when(toMs(a.bis)!) : "–"}</dt>
          <dd>${eur(a.ersparnis_eur)}</dd>
        </div>
      </dl>
      <div class="next">
        ${next
          ? html`<b>Als Nächstes:</b>
              <span class="num-tab">${when(next.von)}–${time(next.bis)}</span>
              <span class="pill" style=${pillColor(next.was)}>${ACTIONS[next.was].name}</span>
              <span class="muted"
                >spart ${eur(next.spart)}${charge > 0 && next.was === "l"
                  ? ` · ${num(charge, 1)} kWh aus dem Netz`
                  : ""}</span
              >`
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
      // move focus into the question, so keyboard and screen-reader users notice it
      this.updateComplete.then(() => this.renderRoot.querySelector<HTMLElement>(".confirm .action")?.focus());
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
    const ist: string | undefined = target?.attributes.ist;
    const options: string[] = mode.attributes.options ?? MODES.map((m) => m.name);
    const feedback = this.visibleFeedback("control");
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
        .mode[aria-disabled="true"] {
          opacity: 0.6;
          cursor: progress;
        }
        .mode[aria-checked="true"] .text {
          /* secondary text on the primary tint: mixed from the text colour, not grey (contrast ≥ 4.5:1) */
          color: color-mix(in srgb, var(--primary-text-color) 78%, var(--primary-color));
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
            aria-disabled=${this.busy}
            @click=${() => !this.busy && this.selectMode(mode, o)}
            @keydown=${(ev: KeyboardEvent) => this.onModeKey(ev, mode, options)}
          >
            <span class="dot" aria-hidden="true"></span><span class="name">${o}</span>
            ${m ? html`<span class="text">${m.text}</span>` : nothing}
          </button>`;
        })}
      </div>
      ${this.confirmMode
        ? html`<div
            class="confirm"
            role="alertdialog"
            aria-label="Modus wechseln"
            @keydown=${(ev: KeyboardEvent) => {
              if (ev.key !== "Escape") return;
              this.confirmMode = undefined;
              this.updateComplete.then(() =>
                this.renderRoot.querySelector<HTMLElement>('.mode[aria-checked="true"]')?.focus(),
              );
            }}
          >
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
              <dd>${when(until)}</dd>`
          : nothing}
      </dl>
      ${ist === "Nicht erreichbar"
        ? html`<div class="warn error">
            Der Wechselrichter meldet keinen Zustand. Das EMS schaltet erst wieder, wenn er erreichbar ist.
          </div>`
        : nothing}
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
      ["Strompreis: günstig", COLORS.cheap],
      ["mittel", COLORS.middle],
      ["teuer", COLORS.expensive],
      ["Eigenverbrauch", ACTIONS.n.color],
      ["Entladung gesperrt", ACTIONS.h.color],
      ["Laden aus dem Netz", ACTIONS.l.color],
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
          gap: 6px 18px;
          margin: 8px 0 0;
          padding: 0;
          list-style: none;
          font-size: 0.95rem;
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
          width: 14px;
          height: 3px;
          border-radius: 2px;
        }
        .legend i.band {
          width: 14px;
          opacity: 0.35;
        }
        .legend i.dash {
          width: 14px;
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
        ? html`<p class="explain" style="margin:0 0 12px">
              <b>So liest du das:</b> Die Balken zeigen den Strompreis, von grün (günstig) bis rot (teuer). Die
              Streifen darunter zeigen, was Akku und Wärmepumpe in jeder Viertelstunde tun, die Kurve darunter den
              Akkustand mit Plan (und gestrichelt ohne Plan).
            </p>
            <ems-chart .option=${this.option(slots)} .height=${690} .label=${chartSummary(slots)}></ems-chart>
            <ul class="legend muted" aria-label="Legende">
              ${legend.map(
                ([n, c, kind]) =>
                  html`<li><i class=${kind ?? ""} style="background:${c};border-color:${c}"></i>${n}</li>`,
              )}
            </ul>
            <p class="explain small muted">
              Zum Zoomen ziehen oder scrollen, antippen für die Werte einer Viertelstunde.
            </p>`
        : html`<div class="muted">Kein Plan.</div>`}`;
  }

  private renderBlocks() {
    const plan = sensor(this.hass, this.config, "plan");
    const hp = sensor(this.hass, this.config, "waermepumpe");
    const blocks: PlanBlock[] = plan?.attributes.aktionen ?? [];
    const boost: { von: number; bis: number }[] = plan?.attributes.wp_aktionen ?? [];
    // the plan's total, not the sum of the blocks: each block is compared with the plan without it, so the
    // block savings overlap and do not add up
    const total = Number(plan?.attributes.ersparnis_eur) || 0;
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
              <div class="total"><span>Ganzer Plan, nach Verlusten und Verschleiß</span><span>${eur(total)}</span></div>
              ${blocks.length > 1
                ? html`<p class="muted small" style="margin:6px 0 0">
                    Die Beträge der einzelnen Aktionen hängen zusammen und ergeben addiert mehr als der ganze Plan.
                  </p>`
                : nothing}`
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
