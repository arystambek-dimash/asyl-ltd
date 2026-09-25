import { describe, expect, it } from "vitest";
import { railReportBody, reportDayLabel } from "./rail-report";

describe("rail report", () => {
  it("sends the order only for «Отгрузить по отчёту»", () => {
    expect(railReportBody("отчёт", null)).toEqual({ text: "отчёт" });
    expect(railReportBody("отчёт", 42)).toEqual({ text: "отчёт", order: 42 });
  });

  it("labels the report day with its weekday", () => {
    expect(reportDayLabel("2026-09-19")).toBe("сб 19.09.2026");
    expect(reportDayLabel(null)).toBe("без даты");
  });
});
