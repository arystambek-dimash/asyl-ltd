import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText, railReportBody, reportDayLabel } from "./rail-report";

describe("rail report", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the order only for «Отгрузить по отчёту»", () => {
    expect(railReportBody("отчёт", null)).toEqual({ text: "отчёт" });
    expect(railReportBody("отчёт", 42)).toEqual({ text: "отчёт", order: 42 });
  });

  it("labels the report day with its weekday", () => {
    expect(reportDayLabel("2026-09-19")).toBe("сб 19.09.2026");
    expect(reportDayLabel(null)).toBe("без даты");
  });

  it("copies through the Clipboard API when it is there", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });

    await expect(copyText("сб 19.09.26")).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith("сб 19.09.26");
  });

  it("falls back to a hidden field without the Clipboard API", async () => {
    vi.stubGlobal("navigator", {});
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec;

    await expect(copyText("отчёт")).resolves.toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
    expect(document.querySelector("textarea")).toBeNull();
  });
});
