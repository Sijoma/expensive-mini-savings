import { describe, expect, it } from "vitest";
import { QUARTER_MS, historyOption, intervals, midnights, planOption, savingsOption } from "../src/options";
import { summary } from "../src/views/history-view";
import { cause, logbookRows } from "../src/logbook";
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

describe("planOption", () => {
  it("is empty without slots", () => {
    expect(planOption([], T0)).toEqual({});
  });

  it("colors price bars by action and marks the heat pump boost", () => {
    const slots = [slot(0), slot(1, "h"), slot(2, "l", "pv"), slot(3, "n", "pv")];
    const o = planOption(slots, T0) as any;
    const price = o.series.find((s: any) => s.name === "Preis");
    const colors = price.data.map((d: any) => d.itemStyle.color);
    expect(new Set(colors).size).toBe(3);
    const pv = o.series.find((s: any) => s.name === "PV");
    expect(pv.markArea.data).toHaveLength(1);
    expect(o.xAxis).toHaveLength(3);
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
    expect(text).toContain("30 ct");
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
    expect(rows[1].what).toBe("→ Self-consumption mode (default)");
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
    expect(logbookRows(entries).map((r) => r.what)).toEqual(["→ Self", "→ VPP", "→ Self"]);
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
