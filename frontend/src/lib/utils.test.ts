import { describe, expect, it } from "vitest";
import { analyticsRange, apiUrl, bagsLabel, dateRangeError, formatCount, loadErrorText, toggledSet } from "./utils";

describe("apiUrl", () => {
  it("пропускает пустые значения и «all»", () => {
    expect(apiUrl("/reports/summary/", { date_from: "2026-09-01", date_to: "", department: "all" })).toBe(
      "/reports/summary/?date_from=2026-09-01",
    );
  });

  it("без параметров — голый путь", () => {
    expect(apiUrl("/reports/summary/", { date_from: "", department: "all" })).toBe("/reports/summary/");
  });
});

describe("dateRangeError", () => {
  it("ругается только на перевёрнутый период", () => {
    expect(dateRangeError("2026-09-10", "2026-09-01")).toBe("Дата начала не может быть позже даты окончания.");
    expect(dateRangeError("2026-09-01", "2026-09-01")).toBeNull();
    expect(dateRangeError("2026-09-10", "")).toBeNull();
  });
});

describe("bagsLabel", () => {
  it("склоняет «мешок» по числу", () => {
    expect([1, 3, 5, 11, 21].map(bagsLabel)).toEqual(["1 мешок", "3 мешка", "5 мешков", "11 мешков", "21 мешок"]);
  });
});

describe("analyticsRange", () => {
  it("считает дни включительно и принимает от 1 до 366 дней", () => {
    expect(analyticsRange("2026-09-24", "2026-09-24")).toEqual({ days: 1, valid: true });
    expect(analyticsRange("2026-09-18", "2026-09-24")).toEqual({ days: 7, valid: true });
    expect(analyticsRange("2025-09-24", "2026-09-24")).toEqual({ days: 366, valid: true });
  });

  it("отклоняет перевёрнутый, слишком длинный и пустой период", () => {
    expect(analyticsRange("2026-09-25", "2026-09-24").valid).toBe(false);
    expect(analyticsRange("2025-09-23", "2026-09-24").valid).toBe(false);
    expect(analyticsRange("", "2026-09-24").valid).toBe(false);
  });
});

describe("formatCount", () => {
  it("делит разряды и не округлит штуки как деньги", () => {
    expect(formatCount(12345).replace(/\s/g, " ")).toBe("12 345");
    expect(formatCount(0)).toBe("0");
  });
});

describe("loadErrorText", () => {
  it("берёт текст сервера, а на пустой ответ с кодом (403) — запасной", () => {
    expect(loadErrorText({ error: "Нет связи", errorStatus: 500 }, "Недоступно")).toBe("Нет связи");
    expect(loadErrorText({ error: "", errorStatus: 403 }, "Недоступно")).toBe("Недоступно");
    expect(loadErrorText({ error: "", errorStatus: null }, "Недоступно")).toBe("");
  });
});

describe("toggledSet", () => {
  it("убирает имеющийся элемент и добавляет новый, не трогая исходный Set", () => {
    const source = new Set(["a", "b"]);
    expect([...toggledSet(source, "a")]).toEqual(["b"]);
    expect([...toggledSet(source, "c")]).toEqual(["a", "b", "c"]);
    expect([...source]).toEqual(["a", "b"]);
  });
});
