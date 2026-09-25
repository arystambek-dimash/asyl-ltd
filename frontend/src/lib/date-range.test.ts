import { describe, expect, it } from "vitest";
import { periodPresetOf, periodRange, type PeriodOption } from "./date-range";

const today = "2026-09-12";

describe("periodRange", () => {
  it("builds presets from today", () => {
    expect(periodRange("today", today)).toEqual({ dateFrom: "2026-09-12", dateTo: "2026-09-12" });
    expect(periodRange("yesterday", today)).toEqual({ dateFrom: "2026-09-11", dateTo: "2026-09-11" });
    expect(periodRange("week", today)).toEqual({ dateFrom: "2026-09-06", dateTo: "2026-09-12" });
    expect(periodRange("last30", today)).toEqual({ dateFrom: "2026-08-14", dateTo: "2026-09-12" });
    expect(periodRange("month", today)).toEqual({ dateFrom: "2026-09-01", dateTo: "2026-09-12" });
    expect(periodRange("all", today)).toEqual({ dateFrom: "", dateTo: "" });
  });

  it("crosses month and year boundaries", () => {
    expect(periodRange("yesterday", "2026-01-01")).toEqual({ dateFrom: "2025-12-31", dateTo: "2025-12-31" });
    expect(periodRange("week", "2026-03-03")).toEqual({ dateFrom: "2026-02-25", dateTo: "2026-03-03" });
  });
});

describe("periodPresetOf", () => {
  const options: PeriodOption<"today" | "week" | "all">[] = [
    { key: "today", label: "Сегодня" },
    { key: "week", label: "Неделя" },
    { key: "all", label: "Всё" },
  ];

  it("recognises the active preset among the screen's options", () => {
    expect(periodPresetOf({ dateFrom: "2026-09-12", dateTo: "2026-09-12" }, options, today)).toBe("today");
    expect(periodPresetOf({ dateFrom: "2026-09-06", dateTo: "2026-09-12" }, options, today)).toBe("week");
    expect(periodPresetOf({ dateFrom: "", dateTo: "" }, options, today)).toBe("all");
  });

  it("returns custom for a range outside the options", () => {
    expect(periodPresetOf({ dateFrom: "2026-09-02", dateTo: "2026-09-12" }, options, today)).toBe("custom");
    expect(periodPresetOf({ dateFrom: "2026-09-11", dateTo: "2026-09-11" }, options, today)).toBe("custom");
  });
});
