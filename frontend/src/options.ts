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
    label: {
      formatter: new Date(t).toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }),
      position: "insideEndTop",
      color: "#8a8a85",
      textBorderWidth: 0,
      fontSize: 10,
    },
  }));

/** The main chart: price by action and value of 1 kWh / SoC with and without plan / PV, load, boost. */
export function planOption(slots: SlotRow[], now: number, accent = "#03a9f4"): ChartOption {
  if (!slots.length) return {};
  const first = slots[0][0];
  const last = slots[slots.length - 1][0] + QUARTER_MS;
  const starts = slots.map((s) => s[0]);
  const boost = intervals(
    starts,
    slots.map((s) => s[6] === "pv"),
  );
  const grids = [
    { top: 28, height: 150, left: 44, right: 44 },
    { top: 222, height: 100, left: 44, right: 44 },
    { top: 366, height: 100, left: 44, right: 44 },
  ];
  const x = (i: number, show: boolean) => ({
    type: "time",
    gridIndex: i,
    min: first,
    max: last,
    ...axisStyle(),
    axisLabel: { show, hideOverlap: true, formatter: (v: number) => time(v) },
    splitLine: { show: false },
    axisPointer: { show: true, label: { show: false } },
  });
  const nowLine = {
    silent: true,
    symbol: "none",
    data: [
      {
        xAxis: now,
        lineStyle: { color: accent, type: "dashed", width: 1.5 },
        label: { show: false },
      },
      ...dayLines(first, last),
    ],
  };
  const byStart = new Map(slots.map((s) => [s[0], s]));

  return {
    grid: grids,
    xAxis: [x(0, false), x(1, false), x(2, true)],
    yAxis: [
      { type: "value", gridIndex: 0, name: "ct/kWh", ...axisStyle(), splitNumber: 3 },
      { type: "value", gridIndex: 1, name: "Akku %", min: 0, max: 100, interval: 50, ...axisStyle() },
      { type: "value", gridIndex: 2, name: "kW", min: 0, ...axisStyle(), splitNumber: 2 },
    ],
    axisPointer: { link: [{ xAxisIndex: "all" }] },
    dataZoom: [{ type: "inside", xAxisIndex: [0, 1, 2], minValueSpan: 3 * 3600_000 }],
    tooltip: {
      trigger: "axis",
      formatter: (params: { axisValue: number }[]) => {
        const t = params[0]?.axisValue;
        if (t == null) return "";
        const s = byStart.get(t - (t % QUARTER_MS)) ?? byStart.get(t - QUARTER_MS - (t % QUARTER_MS));
        if (!s) return "";
        const a = ACTIONS[s[5]] ?? ACTIONS.n;
        const charge = s[7] > 0 ? `<br>aus dem Netz: ${num(s[7], 2)} kWh` : "";
        const wp = s[6] === "pv" ? "<br>Wärmepumpe: PV-Boost" : "";
        return (
          `<b>${when(s[0])}–${time(s[0] + QUARTER_MS)}</b><br>` +
          `<span style="display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;background:${a.color}"></span>${a.name} · ${ct(s[1])}${charge}<br>` +
          `1 kWh im Akku: ${ct(s[8])}<br>` +
          `Akku: ${s[4]} % (ohne Plan ${s[9]} %)<br>` +
          `PV ${num(s[2], 2)} kW · Verbrauch ${num(s[3], 2)} kW${wp}`
        );
      },
    },
    series: [
      {
        name: "Preis",
        type: "bar",
        xAxisIndex: 0,
        yAxisIndex: 0,
        barWidth: "85%",
        barCategoryGap: "10%",
        data: slots.map((s) => ({
          value: [s[0] + QUARTER_MS / 2, s[1]],
          itemStyle: { color: (ACTIONS[s[5]] ?? ACTIONS.n).color, borderRadius: [2, 2, 0, 0] },
        })),
        markLine: nowLine,
      },
      {
        name: "1 kWh im Akku",
        type: "line",
        xAxisIndex: 0,
        yAxisIndex: 0,
        step: "start",
        symbol: "none",
        lineStyle: { width: 2, color: COLORS.value },
        itemStyle: { color: COLORS.value },
        data: slots.map((s) => [s[0], s[8]]),
      },
      {
        name: "Akku mit Plan",
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        symbol: "none",
        lineStyle: { width: 2.5, color: COLORS.soc },
        itemStyle: { color: COLORS.soc },
        areaStyle: { color: COLORS.soc, opacity: 0.12 },
        data: slots.map((s) => [s[0] + QUARTER_MS, s[4]]),
        markLine: { ...nowLine, data: nowLine.data.slice(0, 1).map((d) => ({ ...d, label: { show: false } })) },
      },
      {
        name: "Akku ohne Plan",
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        symbol: "none",
        lineStyle: { width: 1.5, type: "dashed", color: COLORS.socWithout },
        itemStyle: { color: COLORS.socWithout },
        data: slots.map((s) => [s[0] + QUARTER_MS, s[9]]),
      },
      {
        name: "PV",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        symbol: "none",
        smooth: 0.3,
        lineStyle: { width: 1.5, color: COLORS.pv },
        itemStyle: { color: COLORS.pv },
        areaStyle: { color: COLORS.pv, opacity: 0.3 },
        data: slots.map((s) => [s[0] + QUARTER_MS / 2, s[2]]),
        markArea: {
          silent: true,
          itemStyle: { color: COLORS.boost, opacity: 0.14 },
          label: { show: true, position: "insideTop", formatter: "PV-Boost", color: COLORS.boost, fontSize: 10 },
          data: boost.map(([a, b]) => [{ xAxis: a }, { xAxis: b }]),
        },
      },
      {
        name: "Verbrauch",
        type: "line",
        xAxisIndex: 2,
        yAxisIndex: 2,
        step: "start",
        symbol: "none",
        lineStyle: { width: 1.5, color: COLORS.load },
        itemStyle: { color: COLORS.load },
        data: slots.map((s) => [s[0], s[3]]),
      },
    ],
  };
}

const ROW_PLAN = 1;
const ROW_DONE = 0;

/** Plan against reality: measured SoC (and planned, if recorded), planned and executed action, price. */
export function historyOption(rows: HistoryRow[]): ChartOption {
  if (!rows.length) return {};
  const first = rows[0].start;
  const last = rows[rows.length - 1].start + QUARTER_MS;
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
      { type: "value", gridIndex: 2, name: "ct/kWh", ...axisStyle(), splitNumber: 2 },
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
        lineStyle: { width: 1.5, type: "dashed", color: COLORS.socWithout },
        data: rows.map((r) => [r.start + QUARTER_MS, r.plan_soc]),
      },
      {
        name: "Aktion",
        type: "custom",
        xAxisIndex: 1,
        yAxisIndex: 1,
        encode: { x: 0, y: 1 },
        data: strip,
        renderItem: (_params: unknown, api: any) => {
          const start = api.value(0);
          const row = api.value(1);
          const code = String(api.value(2));
          const [x0, y] = api.coord([start, row]);
          const [x1] = api.coord([start + QUARTER_MS, row]);
          const h = api.size([0, 1])[1] * 0.7;
          return {
            type: "rect",
            shape: { x: x0, y: y - h / 2, width: Math.max(x1 - x0 - 0.5, 1), height: h },
            style: { fill: (ACTIONS[code] ?? ACTIONS.n).color },
          };
        },
      },
      {
        name: "Preis",
        type: "bar",
        xAxisIndex: 2,
        yAxisIndex: 2,
        barWidth: "85%",
        data: rows.map((r) => ({
          value: [r.start + QUARTER_MS / 2, r.preis_ct],
          itemStyle: { color: (ACTIONS[r.aktion ?? "n"] ?? ACTIONS.n).color },
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
      { name: "Prognose", type: "bar", data: forecast, itemStyle: { color: COLORS.forecast, borderRadius: [3, 3, 0, 0] } },
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
          itemStyle: { color: h === hour ? COLORS.soc : COLORS.forecast, borderRadius: [3, 3, 0, 0] },
        })),
      },
    ],
  };
}
