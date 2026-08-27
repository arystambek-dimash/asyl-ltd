import { describe, expect, it } from "vitest";

import { brandMeta, isKnownBrand, normalizedBrand } from "./monoblock-brands";

describe("monoblock brand labels", () => {
  it("показывает известные классы модели человеческими названиями", () => {
    expect(brandMeta("korol").label).toBe("Korol");
    expect(brandMeta("DIKHAN_BABA").label).toBe("Дихан Баба");
    expect(isKnownBrand("dikhan_baba")).toBe(true);
  });

  it("различает неуверенный ответ модели и старые данные", () => {
    expect(brandMeta("unknown").label).toBe("Не распознано");
    expect(brandMeta("unclassified").label).toBe("Нет данных (старые)");
    expect(isKnownBrand("unknown")).toBe(false);
    expect(isKnownBrand("unclassified")).toBe(false);
  });

  it("безопасно показывает будущий класс бренда", () => {
    expect(normalizedBrand("  New_Brand ")).toBe("new_brand");
    expect(brandMeta("new_brand").label).toBe("New Brand");
  });
});
