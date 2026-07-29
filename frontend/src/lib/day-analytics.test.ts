import { describe, expect, it } from "vitest";
import { dayColorBreakdown, fullDay, shortDay } from "./day-analytics";
import type { AlwaysOnHistoryPoint } from "@/lib/types";

function point(partial: Partial<AlwaysOnHistoryPoint>): AlwaysOnHistoryPoint {
  return {
    day: "2026-07-28",
    model_total: 0,
    model_per_color: {},
    adjustment: 0,
    total: 0,
    updated_at: null,
    ...partial,
  };
}

describe("shortDay / fullDay", () => {
  it("форматирует календарную дату без сдвига таймзоны", () => {
    expect(shortDay("2026-07-28")).toBe("28.07");
    expect(fullDay("2026-07-28")).toBe("28.07.2026");
    expect(fullDay("2026-01-01")).toBe("01.01.2026");
  });
});

describe("dayColorBreakdown", () => {
  it("считает долю от распознанного и сортирует по убыванию", () => {
    const rows = dayColorBreakdown(
      point({ model_per_color: { green: 100, red: 700, blue: 200 }, model_total: 1000 }),
    );
    expect(rows.map((r) => r.color)).toEqual(["red", "blue", "green"]);
    expect(rows.map((r) => r.percent)).toEqual([70, 20, 10]);
    expect(rows.reduce((sum, r) => sum + r.percent, 0)).toBe(100);
  });

  it("берёт базу от суммы цветов, а не от итога с поправкой", () => {
    // Кассир снял 50 мешков вручную: у поправки цвета нет, поэтому доли
    // считаются от 200 распознанных, и в сумме дают 100%.
    const rows = dayColorBreakdown(
      point({ model_per_color: { red: 150, blue: 50 }, model_total: 200, adjustment: -50, total: 150 }),
    );
    expect(rows.map((r) => r.percent)).toEqual([75, 25]);
  });

  it("округляет до десятых", () => {
    const rows = dayColorBreakdown(point({ model_per_color: { red: 1, blue: 2 }, model_total: 3 }));
    expect(rows.map((r) => r.percent)).toEqual([66.7, 33.3]);
  });

  it("отбрасывает нулевые цвета", () => {
    const rows = dayColorBreakdown(point({ model_per_color: { red: 5, white: 0 }, model_total: 5 }));
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ color: "red", total: 5, percent: 100 });
  });

  it("не делит на ноль в пустой день", () => {
    expect(dayColorBreakdown(point({}))).toEqual([]);
    expect(dayColorBreakdown(undefined)).toEqual([]);
  });
});
