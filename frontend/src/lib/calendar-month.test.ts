import { describe, expect, it } from "vitest";
import { monthGrid, monthOf, monthTitle, shiftMonth } from "./calendar-month";

describe("shiftMonth", () => {
  it("crosses the year boundary in both directions", () => {
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftMonth("2025-12", 1)).toBe("2026-01");
  });
});

describe("monthTitle", () => {
  it("names the month in Russian", () => {
    expect(monthTitle("2026-09")).toBe("Сентябрь 2026");
  });
});

describe("monthOf", () => {
  it("takes the month of a day", () => {
    expect(monthOf("2026-09-18")).toBe("2026-09");
  });
});

describe("monthGrid", () => {
  it("starts on Monday and pads the first week with the previous month", () => {
    // 1 сентября 2026 — вторник, поэтому неделя начинается с 31 августа.
    const grid = monthGrid("2026-09");
    expect(grid[0]).toEqual({ iso: "2026-08-31", day: 31, outside: true });
    expect(grid[1]).toEqual({ iso: "2026-09-01", day: 1, outside: false });
    expect(grid.length % 7).toBe(0);
    expect(grid.filter((cell) => !cell.outside)).toHaveLength(30);
  });

  it("covers a month that starts on Monday without an empty leading week", () => {
    const grid = monthGrid("2026-06");
    expect(grid[0]).toEqual({ iso: "2026-06-01", day: 1, outside: false });
    expect(grid.filter((cell) => !cell.outside)).toHaveLength(30);
  });
});
