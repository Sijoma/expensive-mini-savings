import { describe, expect, it } from "vitest";
import {
  QUARTER_MS,
  historyOption,
  intervals,
  midnights,
  planOption,
  priceColor,
  priceAxisMin,
  priceLevel,
  savingsOption,
  stripColor,
  tick,
} from "../src/options";
import { summary } from "../src/views/history-view";
import { cause, logbookRows } from "../src/logbook";
import { plainState, plainText } from "../src/texts";
import { day, eur, num, when } from "../src/format";
import { decisions } from "../src/views/log-view";
import { batteryLasts, blockSentence, chartSummary, lowPoint } from "../src/views/plan-view";
import type { HistoryRow, SlotRow } from "../src/types";

const T0 = Date.parse("2026-11-09T22:00:00+01:00");

function slot(i: number, action = "n", hp = "n"): SlotRow {
  return [T0 + i * QUARTER_MS, 30 + i, 0, 0.5, 50, action, hp, 0, 20, 40, 0, 0.5, 0, 0, 0, 0];
}

describe("intervals", () => {
  it("joins consecutive quarter hours", () => {
    const starts = [0, 1, 2, 3, 4].map((i) => i * QUARTER_MS);
    expect(intervals(starts, [true, true, false, true, false])).toEqual([
      [0, 2 * QUARTER_MS],
      [3 * QUARTER_MS, 4 * QUARTER_MS],
    ]);
  });
});

describe("midnights", () => {
  it("lists each local midnight in the range", () => {
    const m = midnights(T0, T0 + 20 * 3600_000); // 22:00 → 18:00 next day
    expect(m).toHaveLength(1);
    expect(new Date(m[0]).getHours()).toBe(0);
  });
});

describe("priceAxisMin", () => {
  it("starts below the cheapest price, on a 5 ct step", () => {
    expect(priceAxisMin([34.6, 41, 82.8])).toBe(20); // 34.6 − 14.5 = 20.1
    expect(priceAxisMin([41.1, 92.0])).toBe(25); // 41.1 − 15.3 = 25.8
    expect(priceAxisMin([45, 46])).toBe(40); // at least 5 ct room
    expect(priceAxisMin([-3, 20])).toBe(-10);
    expect(priceAxisMin([])).toBe(0);
  });
});

describe("plan price axis", () => {
  it("follows the prices and keeps the value line inside", () => {
    const slots = [slot(0), slot(1), slot(2)];
    slots.forEach((s) => (s[8] = 5)); // value far below the prices (30–32 ct)
    const o = planOption(slots, T0) as any;
    expect(o.yAxis[0].min).toBe(25);
    expect(o.series.find((s: any) => s.name === "1 kWh im Akku")).toBeUndefined();
    expect(o.tooltip.formatter([{ axisValue: T0 + 60_000 }])).toContain("5 ct wert"); // still in the details
  });
});

describe("batteryLasts", () => {
  const withSoc = (socs: number[]) =>
    socs.map((soc, i) => {
      const s = slot(i);
      s[4] = soc;
      return s;
    });
  it("says until when the battery lasts", () => {
    expect(batteryLasts(withSoc([40, 20, 5, 0, 0]), 45)).toEqual({ label: "Akku reicht bis", value: when(T0 + 3 * QUARTER_MS) });
    expect(batteryLasts(withSoc([40, 30, 50]), 45)).toEqual({ label: "Akku reicht", value: "bis Planende" });
  });
  it("says when an empty battery charges again", () => {
    expect(batteryLasts(withSoc([0, 0, 12, 24]), 0)).toEqual({
      label: "Akku ist leer, lädt wieder ab",
      value: when(T0 + 2 * QUARTER_MS),
    });
    expect(batteryLasts(withSoc([0, 0]), 0).value).toBe("im ganzen Plan");
  });
});

describe("tick", () => {
  it("shows the day at midnight and the time otherwise", () => {
    expect(tick(Date.parse("2026-11-10T00:00:00+01:00"))).toBe("Di. 10.11.");
    expect(tick(Date.parse("2026-11-10T04:00:00+01:00"))).toBe("04:00");
  });
});

describe("planOption", () => {
  it("is empty without slots", () => {
    expect(planOption([], T0)).toEqual({});
  });

  it("colors price bars by level and shows the actions as strips", () => {
    const slots = [slot(0), slot(1, "h"), slot(2, "h"), slot(3, "l", "pv"), slot(4, "n", "pv")];
    const o = planOption(slots, T0) as any;
    const price = o.series.find((s: any) => s.name === "Preis");
    const colors = price.data.map((d: any) => d.itemStyle.color);
    expect(colors[0]).toBe("rgb(46,157,74)"); // cheapest: green
    expect(colors[4]).toBe("rgb(217,68,47)"); // most expensive: red
    const strip = o.series.find((s: any) => s.type === "custom");
    expect(strip.data.filter((d: any) => d[1] === 1).map((d: any) => d[2])).toEqual(["n", "h", "h", "l", "n"]);
    expect(strip.data.filter((d: any) => d[1] === 0).map((d: any) => d[2])).toEqual(["-", "-", "-", "pv", "pv"]);
    expect(o.xAxis).toHaveLength(4);
    expect(stripColor("pv")).toBe(stripColor("pv"));
  });

  it("names the price level in thirds of the range", () => {
    expect(priceLevel(41, 41, 82)).toBe("günstig");
    expect(priceLevel(60, 41, 82)).toBe("mittel");
    expect(priceLevel(80, 41, 82)).toBe("teuer");
    expect(priceLevel(30, 30, 30)).toBe("mittel");
    expect(priceColor(61.5, 41, 82)).toBe("rgb(242,183,5)");
  });

  it("finds the lowest planned state of charge", () => {
    const slots = [slot(0), slot(1), slot(2)];
    slots[1][4] = 16;
    expect(lowPoint(slots)).toEqual({ soc: 16, at: T0 + 2 * QUARTER_MS });
    expect(lowPoint([])).toBeNull();
  });

  it("puts the SoC at the end of each quarter hour", () => {
    const o = planOption([slot(0)], T0) as any;
    const soc = o.series.find((s: any) => s.name === "Akku mit Plan");
    expect(soc.data[0][0]).toBe(T0 + QUARTER_MS);
  });

  it("explains a quarter hour in the tooltip", () => {
    const o = planOption([slot(0, "h")], T0) as any;
    const text = o.tooltip.formatter([{ axisValue: T0 + 60_000 }]);
    expect(text).toContain("Entladung gesperrt");
    expect(text).toContain("Strom 30 ct");
  });
});

function row(i: number, aktion: string | null, plan: string | null, bezug = 0.25): HistoryRow {
  return {
    start: T0 + i * QUARTER_MS,
    preis_ct: 40,
    soc: 50,
    aktion,
    plan_aktion: plan,
    plan_soc: null,
    pv_kw: 0,
    last_kw: 1,
    bezug_kwh: bezug,
    einspeisung_kwh: 0,
  };
}

describe("history", () => {
  it("counts quarter hours that went as planned (old-style stop counts as hold)", () => {
    const s = summary([row(0, "n", "n"), row(1, "f", "h"), row(2, "v", "n"), row(3, "n", null)]);
    expect(s.withPlan).toBe(3);
    expect(s.same).toBe(2);
    expect(s.importKwh).toBeCloseTo(1);
    expect(s.cost).toBeCloseTo(0.4);
    expect(s.avg).toBeCloseTo(40);
  });

  it("draws planned and executed action strips", () => {
    const o = historyOption([row(0, "n", "h"), row(1, "h", null)]) as any;
    const strip = o.series.find((s: any) => s.type === "custom");
    expect(strip.data).toHaveLength(3);
  });
});

describe("savingsOption", () => {
  it("starts at the first day with data", () => {
    const o = savingsOption(["2026-11-01", "2026-11-02", "2026-11-03"], [null, 0.1, null], [null, null, 0.2]) as any;
    expect(o.xAxis[0].data).toEqual(["02.11.", "03.11."]);
    expect(o.series[0].data).toEqual([0.1, null]);
  });
});

describe("logbook", () => {
  const base = { entity_id: "select.ems_mode", name: "EMS mode" };
  it("names who caused a change", () => {
    expect(cause({ when: 0, context_user_id: "u1" }, "u1").by).toBe("du");
    expect(cause({ when: 0, context_user_id: "u2" }, "u1").by).toBe("Benutzer");
    expect(
      cause({ when: 0, context_event_type: "automation_triggered", context_name: "EMS: Akku steuern" }).by,
    ).toBe("Automation „EMS: Akku steuern“");
    expect(cause({ when: 0, context_entity_id: "sensor.x", context_entity_id_name: "X" }).kind).toBe("entity");
    expect(cause({ when: 0 }).kind).toBe("device");
  });

  it("sorts newest first and reads both time formats", () => {
    const rows = logbookRows([
      { ...base, when: "2026-11-08T02:20:17+00:00", state: "Self-consumption mode (default)" },
      { ...base, when: Date.parse("2026-11-09T10:00:00Z") / 1000, message: "changed to VPP" },
      { when: 0, message: "no entity" },
    ]);
    expect(rows).toHaveLength(2);
    expect(rows[0].what).toBe("changed to VPP");
    expect(rows[1].what).toBe("→ Eigenverbrauch");
  });
});

describe("logbook filter", () => {
  const e = (min: number, state: string, extra = {}) => ({
    entity_id: "select.ems_mode",
    name: "EMS mode",
    when: `2026-11-09T10:${String(min).padStart(2, "0")}:00Z`,
    state,
    ...extra,
  });
  it("hides connection drops and the return to the same state", () => {
    const entries = [e(0, "Self"), e(1, "unavailable"), e(2, "Self"), e(3, "VPP"), e(4, "unknown"), e(5, "Self")];
    expect(logbookRows(entries).map((r) => r.what)).toEqual(["→ Self", "→ Anbieter steuert", "→ Self"]);
    expect(logbookRows(entries, undefined, true)).toHaveLength(6);
  });
  it("shows a script run once", () => {
    const s = (state?: string, message?: string) => ({
      entity_id: "script.ems_akku_freigeben",
      name: "Freigeben",
      when: "2026-11-09T10:00:00Z",
      state,
      message,
    });
    const rows = logbookRows([s(undefined, "started"), s("on"), s("off")]);
    expect(rows.map((r) => r.what)).toEqual(["gestartet"]);
  });
});

describe("external inverter changes", () => {
  it("counts the integration's own switch (its logbook entry after the confirmation) as EMS", () => {
    const at = (sec: number) => `2026-11-09T10:00:${String(sec).padStart(2, "0")}Z`;
    const rows = logbookRows(
      [
        { entity_id: "select.ems_mode", name: "EMS mode", when: at(0), state: "Forced mode" },
        { entity_id: "sensor.ems_akku_soll", name: "EMS Akku", when: at(40), message: "Eigenverbrauch → Laden aus dem Netz" },
      ],
      undefined,
      false,
      new Set(["select.ems_mode"]),
      "sensor.ems_akku_soll",
    );
    expect(rows.find((r) => r.entity === "select.ems_mode")!.kind).not.toBe("external");
  });

  const inv = new Set(["select.ems_mode"]);
  const at = (sec: number) => `2026-11-09T10:00:${String(sec).padStart(2, "0")}Z`;
  it("marks an inverter change without EMS action as external", () => {
    const rows = logbookRows([{ entity_id: "select.ems_mode", name: "EMS mode", when: at(0), state: "VPP" }], undefined, false, inv);
    expect(rows[0].kind).toBe("external");
  });
  it("attributes a change right after an EMS automation to the EMS", () => {
    const rows = logbookRows(
      [
        { entity_id: "sensor.soll", name: "Soll", when: at(0), message: "→ x", context_event_type: "automation_triggered", context_name: "EMS" },
        { entity_id: "select.ems_mode", name: "EMS mode", when: at(30), state: "Forced mode", context_entity_id: "sensor.raw" },
      ],
      undefined,
      false,
      inv,
    );
    expect(rows[0].kind).toBe("entity");
    expect(rows[0].by).toBe("Wechselrichter, nach EMS-Befehl");
  });
});

describe("plain language", () => {
  it("translates inverter states and cleans backend sentences", () => {
    expect(plainState("Self-consumption mode (default)")).toBe("Eigenverbrauch");
    expect(plainState("Etwas Eigenes")).toBe("Etwas Eigenes");
    expect(plainText("Anders (unavailable / Stop (default)) → Eigenverbrauch")).toBe(
      "Anders (nicht erreichbar / Stopp) → Eigenverbrauch",
    );
    expect(plainText("None → unavailable nicht bestätigt")).toBe("– → nicht erreichbar nicht bestätigt");
    expect(plainText("Plan 01:00–07:00, Ø 38.8 ct")).toBe("Plan 01:00–07:00, Ø 38,8 ct");
    expect(plainText("Freigegeben bis 10.11. 14:00")).toBe("Freigegeben bis 10.11. 14:00");
    expect(plainText("-0,0 kWh geladen")).toBe("0,0 kWh geladen");
  });

  it("never shows a negative zero", () => {
    expect(num(-0.04, 1)).toBe("0,0");
    expect(eur(-0.004)).toBe("0,00 €");
    expect(eur(-0.04)).toBe("-0,04 €");
  });

  it("formats ISO days in German", () => {
    expect(day("2026-11-09")).toBe("09.11.2026");
  });
});

describe("decisions", () => {
  const e = (art: string, text: string) => ({ zeit: "2026-11-09T10:00:00+01:00", art, text });
  it("collapses repeats, drops empty bookings and filters by actor", () => {
    const entries = [
      e("Freigabe", "Freigegeben bis 08.11."),
      e("Freigabe", "Freigegeben bis 08.11."),
      e("Abgerechnet", "Sperre seit 01:00: 0,0 kWh aus dem Netz, spart geschätzt 0,00 €"),
      e("Geschaltet", "Eigenverbrauch → Laden aus dem Netz (Ø 36.2 ct)"),
    ];
    const all = decisions(entries, "Alle");
    expect(all.map((d) => d.count)).toEqual([2, 1]);
    expect(all[1].item.text).toContain("36,2 ct");
    expect(decisions(entries, "Du")).toHaveLength(1);
    expect(decisions(entries, "von außen")).toHaveLength(0);
  });
});

describe("plan texts", () => {
  it("says in plain words what a block does", () => {
    const b = { von: 0, bis: 1, ct: 40, spart: 0.5 };
    expect(blockSentence({ ...b, was: "h", kwh: 0 })).toBe("Der Akku hält seine Ladung und entlädt nicht");
    expect(blockSentence({ ...b, was: "l", kwh: 1.2 })).toBe("Der Akku lädt 1,2 kWh aus dem Netz");
  });
  it("summarizes the chart for screen readers", () => {
    const text = chartSummary([slot(0), slot(1, "h"), slot(2, "h"), slot(3, "l")]);
    expect(text).toContain("0,5 Stunden Entladung gesperrt");
    expect(text).toContain("0,3 Stunden Laden aus dem Netz");
    expect(text).toContain("Preis zwischen 30 ct und 33 ct");
  });
});
