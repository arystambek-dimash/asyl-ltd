import { describe, expect, it } from "vitest";
import {
  EMPTY_CASH_FILTERS,
  activeFilterCount,
  filterScreenFor,
  initialFilters,
  periodPresetOf,
  periodRange,
} from "./filters";

const now = new Date(2026, 8, 12);

describe("periodRange", () => {
  it("builds presets from today", () => {
    expect(periodRange("today", now)).toEqual({ dateFrom: "2026-09-12", dateTo: "2026-09-12" });
    expect(periodRange("week", now)).toEqual({ dateFrom: "2026-09-06", dateTo: "2026-09-12" });
    expect(periodRange("month", now)).toEqual({ dateFrom: "2026-09-01", dateTo: "2026-09-12" });
    expect(periodRange("all", now)).toEqual({ dateFrom: "", dateTo: "" });
  });
  it("recognises the active preset", () => {
    expect(periodPresetOf({ dateFrom: "2026-09-12", dateTo: "2026-09-12" }, now)).toBe("today");
    expect(periodPresetOf({ dateFrom: "", dateTo: "" }, now)).toBe("all");
    expect(periodPresetOf({ dateFrom: "2026-09-02", dateTo: "2026-09-12" }, now)).toBe("custom");
  });
  it("starts the mobile report at today only", () => {
    const filters = initialFilters(now);
    expect(filters.report).toMatchObject({ dateFrom: "2026-09-12", dateTo: "2026-09-12" });
    expect(filters.overview).toEqual(EMPTY_CASH_FILTERS);
  });
});

describe("filterScreenFor", () => {
  it("hides the panel where a screen has no filters", () => {
    expect(filterScreenFor("overview", false)).toBe("overview");
    expect(filterScreenFor("overview", true)).toBeNull();
    expect(filterScreenFor("report", true)).toBe("report");
    expect(filterScreenFor("report", false)).toBeNull();
    expect(filterScreenFor("confirm", true)).toBe("confirm");
    expect(filterScreenFor("transactions", false)).toBeNull();
    expect(filterScreenFor("home", true)).toBeNull();
  });
});

describe("activeFilterCount", () => {
  it("counts only the requested groups", () => {
    const filters = { ...EMPTY_CASH_FILTERS, dateFrom: "2026-09-01", department: "main", remainingMin: "10" };
    expect(activeFilterCount(filters)).toBe(2);
    expect(activeFilterCount(filters, { remaining: true })).toBe(3);
    expect(activeFilterCount(filters, { dates: false })).toBe(1);
  });
});
