// Chart options as pure functions of the data, so they can be tested without a browser.

import { axisStyle, type ChartOption } from "./chart";
import { ct, num, time, when } from "./format";
import { ACTIONS, COLORS } from "./theme";
import type { HistoryRow, SlotRow } from "./types";

export const QUARTER_MS = 900_000;

/** Consecutive quarter hours with a matching code → [start, end] intervals in ms. */
export function intervals(starts: number[], match: boolean[]): [number, number][] {
  const out: [number, number][] = [];
  starts.forEach((t, i) => {
    if (!match[i]) return;
    const last = out[out.length - 1];
    if (last && last[1] === t) last[1] = t + QUARTER_MS;
    else out.push([t, t + QUARTER_MS]);
  });
  return out;
}

/** Midnights between the first and last time, for day separators. */
export function midnights(from: number, to: number): number[] {
  const out: number[] = [];
  const d = new Date(from);
  d.setHours(24, 0, 0, 0);
  for (let t = d.getTime(); t < to; t = new Date(t).setDate(new Date(t).getDate() + 1)) out.push(t);
  return out;
}

const dayLines = (from: number, to: number) =>
  midnights(from, to).map((t) => ({
    xAxis: t,
    lineStyle: { color: "rgba(128,128,128,0.45)", type: "solid", width: 1 },
    label: { show: false },
  }));

/** Axis tick text: the time, and the day at midnight ("Di. 10.11."). */
export function tick(ms: number): string {
  const d = new Date(ms);
  if (d.getHours() === 0 && d.getMinutes() === 0) {
    return d.toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }).replace(",", "");
  }
  return time(ms);
}

const PRICE_STOPS: [number, [number, number, number]][] = [
  [0, [46, 157, 74]], // cheap: green
  [0.5, [242, 183, 5]], // middle: amber
  [1, [217, 68, 47]], // expensive: red
];

/** Color of a price between the cheapest and the most expensive one in view (green → amber → red). */
export function priceColor(price: number, min: number, max: number): string {
  const t = max > min ? Math.min(1, Math.max(0, (price - min) / (max - min))) : 0.5;
  const upper = PRICE_STOPS.findIndex(([stop]) => t <= stop);
  const [t1, c1] = PRICE_STOPS[Math.max(upper, 1)];
  const [t0, c0] = PRICE_STOPS[Math.max(upper, 1) - 1];
  const f = (t - t0) / (t1 - t0);
  const mix = c0.map((c, i) => Math.round(c + (c1[i] - c) * f));
  return `rgb(${mix.join(",")})`;
}

/** Lower end of the price axis: below the cheapest price, on a 5 ct step (not 0, so the
 * differences between the hours are visible; below 0 when prices are negative). */
export function priceAxisMin(prices: number[]): number {
  const valid = prices.filter((p) => Number.isFinite(p));
  if (!valid.length) return 0;
  const min = Math.min(...valid);
  const max = Math.max(...valid);
  // 30 % of the range below the cheapest price (at least 5 ct), on a 5 ct step: the cheapest hours
  // keep a visible bar height
  return Math.floor((min - Math.max((max - min) * 0.3, 5)) / 5) * 5;
}

/** "günstig", "mittel" or "teuer": thirds of the range between cheapest and most expensive price. */
export function priceLevel(price: number, min: number, max: number): "günstig" | "mittel" | "teuer" {
  const t = max > min ? (price - min) / (max - min) : 0.5;
  return t < 1 / 3 ? "günstig" : t < 2 / 3 ? "mittel" : "teuer";
}

/** The main chart: price by level / planned actions as strips (battery, heat pump) / SoC with and without plan /
 * PV, load and heat pump boost. */
export function planOption(
  slots: SlotRow[],
  now: number,
  accent = "#03a9f4",
  ink = "#212121",
  compact = false,
): ChartOption {
  if (!slots.length) return {};
  const first = slots[0][0];
  const last = slots[slots.length - 1][0] + QUARTER_MS;
  const starts = slots.map((s) => s[0]);
  const boost = intervals(
    starts,
    slots.map((s) => s[6] === "pv"),
  );
  const prices = slots.map((s) => s[1]);
  const axisMin = priceAxisMin(prices);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  // same left edge for all parts, so the quarter hours line up; room for the strip labels
  const left = compact ? 44 : 96;
  const right = 16;
  const grids = [
    { top: 40, height: 210, left, right },
    { top: 262, height: 58, left, right },
    { top: 362, height: 110, left, right },
    { top: 524, height: 110, left, right },
  ];
  const x = (i: number, show: boolean) => ({
    type: "time",
    gridIndex: i,
    min: first,
    max: last,
    ...axisStyle(),
    axisLabel: { show, hideOverlap: true, fontSize: 13, formatter: (v: number) => tick(v) },
    splitLine: { show: false },
    axisPointer: { show: true, label: { show: false } },
  });
  const y = (i: number, name: string, extra: Record<string, unknown>) => ({
    type: "value",
    gridIndex: i,
    name,
    nameLocation: "end",
    nameGap: 12,
    nameTextStyle: { align: "left", padding: [0, 0, 0, -left + 4], fontSize: 14, fontWeight: 600 },
    ...axisStyle(),
    axisLabel: { fontSize: 13 },
    ...extra,
  });
  const nowLine = {
    silent: true,
    symbol: "none",
    data: [
      { xAxis: now, lineStyle: { color: accent, type: "dashed", width: 2 }, label: { show: false } },
      ...dayLines(first, last),
    ],
  };
  const byStart = new Map(slots.map((s) => [s[0], s]));
  const dot = (color: string) =>
    `<span style="display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;background:${color}"></span>`;

  return {
    grid: grids,
    xAxis: [x(0, false), x(1, false), x(2, false), x(3, true)],
    yAxis: [
      y(0, "Strompreis in ct/kWh", { splitNumber: 4, min: axisMin, scale: true }),
      {
        type: "category",
        gridIndex: 1,
        data: compact ? ["Wärme-\npumpe", "Akku"] : ["Wärmepumpe", "Akku"],
        axisTick: { show: false },
        axisLine: { show: false },
        axisLabel: { fontSize: 13, color: ink },
      },
      y(2, "Akku in %", { min: 0, max: 100, interval: 50 }),
      y(3, "PV und Verbrauch in kW", { min: 0, splitNumber: 2 }),
    ],
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1, 2, 3], minValueSpan: 3 * 3600_000 }],
    tooltip: {
      trigger: "axis",
      textStyle: { fontSize: 14 },
      formatter: (params: { axisValue: number }[]) => {
        const t = params[0]?.axisValue;
        if (t == null) return "";
        const s = byStart.get(t - (t % QUARTER_MS)) ?? byStart.get(t - QUARTER_MS - (t % QUARTER_MS));
        if (!s) return "";
        const a = ACTIONS[s[5]] ?? ACTIONS.n;
        const charge = s[7] > 0 ? ` · ${num(s[7], 2)} kWh aus dem Netz` : "";
        const wp = s[6] === "pv" ? `<br>${dot(COLORS.boost)}Wärmepumpe im PV-Boost` : "";
        return (
          `<b>${when(s[0])}–${time(s[0] + QUARTER_MS)}</b><br>` +
          `${dot(priceColor(s[1], min, max))}Strom ${ct(s[1])} (${priceLevel(s[1], min, max)})<br>` +
          `${dot(a.color)}${a.name}${charge}<br>` +
          `${dot(COLORS.soc)}Akku ${s[4]} % (ohne Plan ${s[9]} %) · 1 kWh darin ist ${ct(s[8])} wert<br>` +
          `${dot(COLORS.pv)}PV ${num(s[2], 2)} kW · Verbrauch ${num(s[3], 2)} kW${wp}`
        );
      },
    },
    series: [
      {
        name: "Preis",
        type: "bar",
        xAxisIndex: 0,
        yAxisIndex: 0,
        barWidth: "82%",
        data: slots.map((s) => ({
          value: [s[0] + QUARTER_MS / 2, s[1]],
          itemStyle: { color: priceColor(s[1], min, max), borderRadius: [2, 2, 0, 0] },
        })),
        markLine: nowLine,
      },
      stripSeries(
        1,
        slots.flatMap((s) => [
          [s[0], 1, s[5]],
          [s[0], 0, s[6] === "pv" ? "pv" : "-"],
        ]),
      ),
      {
        name: "Akku mit Plan",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        symbol: "none",
        lineStyle: { width: 3, color: COLORS.soc },
        itemStyle: { color: COLORS.soc },
        areaStyle: { color: COLORS.soc, opacity: 0.18 },
        data: slots.map((s) => [s[0] + QUARTER_MS, s[4]]),
        markLine: { ...nowLine, data: nowLine.data.slice(0, 1) },
      },
      {
        name: "Akku ohne Plan",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        symbol: "none",
        lineStyle: { width: 2, type: "dashed", color: COLORS.socWithout },
        itemStyle: { color: COLORS.socWithout },
        data: slots.map((s) => [s[0] + QUARTER_MS, s[9]]),
      },
      {
        name: "PV",
        type: "line",
        xAxisIndex: 3,
        yAxisIndex: 3,
        symbol: "none",
        smooth: 0.3,
        lineStyle: { width: 2, color: COLORS.pv },
        itemStyle: { color: COLORS.pv },
        areaStyle: { color: COLORS.pv, opacity: 0.3 },
        data: slots.map((s) => [s[0] + QUARTER_MS / 2, s[2]]),
        markLine: { ...nowLine, data: nowLine.data.slice(0, 1) },
        markArea: {
          silent: true,
          itemStyle: { color: COLORS.boost, opacity: 0.2 },
          label: {
            show: !compact,
            position: "insideTop",
            formatter: "PV-Boost",
            color: ink,
            fontSize: 13,
            fontWeight: 600,
            textBorderWidth: 0,
          },
          data: boost.map(([a, b]) => [{ xAxis: a }, { xAxis: b }]),
        },
      },
      {
        name: "Verbrauch",
        type: "line",
        xAxisIndex: 3,
        yAxisIndex: 3,
        step: "start",
        symbol: "none",
        lineStyle: { width: 2, color: COLORS.load },
        itemStyle: { color: COLORS.load },
        data: slots.map((s) => [s[0], s[3]]),
      },
    ],
  };
}

/** Color of a strip cell: battery codes, and "pv" / "-" for the heat pump row. */
export function stripColor(code: string): string {
  if (code === "pv") return COLORS.boost;
  if (code === "-") return "rgba(141,148,153,0.28)";
  return (ACTIONS[code] ?? ACTIONS.n).color;
}

/** One colored cell per quarter hour and row: data rows are [start ms, row index, code]. */
function stripSeries(axis: number, data: unknown[]) {
  return {
    name: "Aktion",
    type: "custom",
    xAxisIndex: axis,
    yAxisIndex: axis,
    encode: { x: 0, y: 1 },
    data,
    renderItem: (_params: unknown, api: any) => {
      const start = api.value(0);
      const row = api.value(1);
      const code = String(api.value(2));
      const [x0, y] = api.coord([start, row]);
      const [x1] = api.coord([start + QUARTER_MS, row]);
      const h = api.size([0, 1])[1] * 0.72;
      return {
        type: "rect",
        shape: { x: x0, y: y - h / 2, width: Math.max(x1 - x0 - 0.6, 1), height: h, r: 1.5 },
        style: { fill: stripColor(code) },
      };
    },
  };
}

const ROW_PLAN = 1;
const ROW_DONE = 0;

/** Plan against reality: measured SoC (and planned, if recorded), planned and executed action, price. */
export function historyOption(rows: HistoryRow[]): ChartOption {
  if (!rows.length) return {};
  const first = rows[0].start;
  const last = rows[rows.length - 1].start + QUARTER_MS;
  const prices = rows.map((r) => r.preis_ct).filter((p): p is number => p != null);
  const priceMin = Math.min(...prices);
  const priceMax = Math.max(...prices);
  const grids = [
    { top: 28, height: 120, left: 74, right: 20 },
    { top: 176, height: 48, left: 74, right: 20 },
    { top: 252, height: 90, left: 74, right: 20 },
  ];
  const x = (i: number, show: boolean) => ({
    type: "time",
    gridIndex: i,
    min: first,
    max: last,
    ...axisStyle(),
    axisLabel: { show, hideOverlap: true, formatter: (v: number) => time(v) },
    splitLine: { show: false },
  });
  const strip: unknown[] = [];
  for (const r of rows) {
    if (r.plan_aktion) strip.push([r.start, ROW_PLAN, r.plan_aktion]);
    if (r.aktion) strip.push([r.start, ROW_DONE, r.aktion]);
  }
  const byStart = new Map(rows.map((r) => [r.start, r]));
  return {
    grid: grids,
    xAxis: [x(0, false), x(1, false), x(2, true)],
    yAxis: [
      { type: "value", gridIndex: 0, name: "Akku %", min: 0, max: 100, interval: 50, ...axisStyle() },
      {
        type: "category",
        gridIndex: 1,
        data: ["Ausgeführt", "Geplant"],
        axisTick: { show: false },
        axisLine: { show: false },
      },
      {
        type: "value",
        gridIndex: 2,
        name: "ct/kWh",
        ...axisStyle(),
        splitNumber: 2,
        min: priceAxisMin(rows.map((r) => r.preis_ct ?? Number.NaN)),
        scale: true,
      },
    ],
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    tooltip: {
      trigger: "axis",
      formatter: (params: { axisValue: number }[]) => {
        const t = params[0]?.axisValue;
        if (t == null) return "";
        const r = byStart.get(t - (t % QUARTER_MS));
        if (!r) return "";
        const plan = r.plan_aktion ? ACTIONS[r.plan_aktion]?.name : "–";
        const done = r.aktion ? ACTIONS[r.aktion]?.name : "–";
        return (
          `<b>${time(r.start)}–${time(r.start + QUARTER_MS)}</b><br>` +
          `Geplant: ${plan}<br>Ausgeführt: ${done}<br>` +
          `Akku: ${r.soc ?? "–"} %${r.plan_soc != null ? ` (Plan ${r.plan_soc} %)` : ""}<br>` +
          `Preis: ${r.preis_ct != null ? ct(r.preis_ct) : "–"} · Netzbezug ${num(r.bezug_kwh ?? 0, 2)} kWh`
        );
      },
    },
    series: [
      {
        name: "Akku (gemessen)",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "none",
        lineStyle: { width: 2.5, color: COLORS.soc },
        areaStyle: { color: COLORS.soc, opacity: 0.12 },
        data: rows.map((r) => [r.start + QUARTER_MS, r.soc]),
      },
      {
        name: "Akku (Plan)",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "none",
        lineStyle: { width: 1.5, type: "dashed", color: COLORS.soc },
        data: rows.map((r) => [r.start + QUARTER_MS, r.plan_soc]),
      },
      stripSeries(1, strip),
      {
        name: "Preis",
        type: "bar",
        xAxisIndex: 2,
        yAxisIndex: 2,
        barWidth: "85%",
        data: rows.map((r) => ({
          value: [r.start + QUARTER_MS / 2, r.preis_ct],
          itemStyle: { color: r.preis_ct != null ? priceColor(r.preis_ct, priceMin, priceMax) : COLORS.forecast },
        })),
      },
    ],
  };
}

/** Savings per day: measured and forecast (EUR). */
export function savingsOption(days: string[], measured: (number | null)[], forecast: (number | null)[]): ChartOption {
  // start at the first day with data
  const first = days.findIndex((_, i) => measured[i] != null || forecast[i] != null);
  if (first > 0) {
    days = days.slice(first);
    measured = measured.slice(first);
    forecast = forecast.slice(first);
  }
  return {
    grid: { top: 28, left: 44, right: 12, bottom: 28 },
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number | null) => (v == null ? "–" : `${num(v, 2)} €`),
    },
    xAxis: [
      {
        type: "category",
        data: days.map((d) => `${d.slice(8, 10)}.${d.slice(5, 7)}.`),
        ...axisStyle(),
        splitLine: { show: false },
      },
    ],
    yAxis: [{ type: "value", name: "€", ...axisStyle(), splitNumber: 3 }],
    series: [
      { name: "Gemessen", type: "bar", data: measured, itemStyle: { color: COLORS.measured, borderRadius: [3, 3, 0, 0] } },
      { name: "Vom Plan versprochen", type: "bar", data: forecast, itemStyle: { color: COLORS.forecast, borderRadius: [3, 3, 0, 0] } },
    ],
  };
}

/** Load profile: 24 hourly values in W, the current hour highlighted. */
export function profileOption(profile: number[], hour: number): ChartOption {
  return {
    grid: { top: 24, left: 44, right: 12, bottom: 24 },
    tooltip: { trigger: "axis", valueFormatter: (v: number) => `${Math.round(v)} W` },
    xAxis: [
      {
        type: "category",
        data: profile.map((_, h) => `${h}`),
        ...axisStyle(),
        splitLine: { show: false },
      },
    ],
    yAxis: [{ type: "value", name: "W", ...axisStyle(), splitNumber: 3 }],
    series: [
      {
        type: "bar",
        data: profile.map((v, h) => ({
          value: v,
          itemStyle: { color: COLORS.load, opacity: h === hour ? 1 : 0.4, borderRadius: [3, 3, 0, 0] },
        })),
      },
    ],
  };
}
