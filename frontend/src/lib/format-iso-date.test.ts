import { describe, expect, it } from "vitest";
import { formatIsoDate, formatIsoDayMonth, shiftIsoDate } from "./utils";

describe("formatIsoDate", () => {
  // «2026-07-28» — календарная дата без времени. new Date() разбирает её как
  // UTC-полночь, поэтому западнее Гринвича toLocaleDateString отдавал 27.07.
  // Здесь дата читается как локальная и не зависит от таймзоны машины.
  it("отдаёт ту же календарную дату, что пришла с сервера", () => {
    expect(formatIsoDate("2026-07-28")).toBe("28.07.2026");
  });

  it("держит первое января без съезда в прошлый год", () => {
    expect(formatIsoDate("2026-01-01")).toBe("01.01.2026");
  });

  it("возвращает исходную строку, если это не дата", () => {
    expect(formatIsoDate("")).toBe("");
    expect(formatIsoDate("не дата")).toBe("не дата");
  });
});

describe("formatIsoDayMonth", () => {
  it("отдаёт день и месяц, как на плашке дня", () => {
    expect(formatIsoDayMonth("2026-08-11")).toBe("11.08");
  });

  it("возвращает исходную строку, если это не дата", () => {
    expect(formatIsoDayMonth("")).toBe("");
  });
});

describe("shiftIsoDate", () => {
  it("сдвигает календарную дату через границы месяца и года", () => {
    expect(shiftIsoDate("2026-03-01", -1)).toBe("2026-02-28");
    expect(shiftIsoDate("2026-12-31", 1)).toBe("2027-01-01");
    expect(shiftIsoDate("2026-07-28", 0)).toBe("2026-07-28");
  });
});
