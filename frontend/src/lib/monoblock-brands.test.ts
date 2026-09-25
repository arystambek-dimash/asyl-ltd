import { describe, expect, it } from "vitest";

import { brandLabel } from "./monoblock-brands";

describe("monoblock brand labels", () => {
  it("показывает известные классы модели человеческими названиями", () => {
    expect(brandLabel("korol")).toBe("Korol");
    expect(brandLabel("DIKHAN_BABA")).toBe("Дихан Баба");
  });

  it("различает неуверенный ответ модели и старые данные", () => {
    expect(brandLabel("unknown")).toBe("Не распознано");
    expect(brandLabel("unclassified")).toBe("Нет данных (старые)");
  });

  it("безопасно показывает будущий класс бренда", () => {
    expect(brandLabel("  New_Brand ")).toBe("New Brand");
    expect(brandLabel("")).toBe("Не указано");
  });
});
