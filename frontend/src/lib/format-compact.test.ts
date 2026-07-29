import { describe, expect, it } from "vitest";
import { formatCompact, formatCompactCurrency } from "./utils";

/** Intl разделяет разряды неразрывным пробелом (U+00A0), а не обычным. */
const nb = (text: string) => text.replace(/ /g, " ");

describe("formatCompact", () => {
  it("оставляет как есть всё, что короче сокращения", () => {
    // «99 999» короче и точнее, чем «100,0 тыс» — сокращать нечего.
    expect(formatCompact(0)).toBe("0");
    expect(formatCompact(1728)).toBe(nb("1 728"));
    expect(formatCompact(99_999)).toBe(nb("99 999"));
  });

  it("сокращает тысячи", () => {
    expect(formatCompact(100_000)).toBe("100 тыс");
    expect(formatCompact(993_045)).toBe("993 тыс");
  });

  it("сокращает миллионы", () => {
    expect(formatCompact(1_000_000)).toBe("1 млн");
    expect(formatCompact(9_930_456)).toBe("9,93 млн");
    expect(formatCompact(111_026_971)).toBe("111 млн");
  });

  it("сокращает миллиарды", () => {
    expect(formatCompact(4_809_747_848.94)).toBe("4,81 млрд");
    expect(formatCompact(4_860_447_427.96)).toBe("4,86 млрд");
  });

  it("держит знак у отрицательных", () => {
    expect(formatCompact(-11_079)).toBe(nb("-11 079"));
    expect(formatCompact(-4_295_732_000)).toBe("-4,3 млрд");
  });

  it("принимает строку — суммы приходят с бэкенда строками", () => {
    expect(formatCompact("10077481.97")).toBe("10,1 млн");
  });

  it("не падает на мусоре", () => {
    expect(formatCompact("не число")).toBe("0");
    expect(formatCompact(Number.NaN)).toBe("0");
  });
});

describe("formatCompactCurrency", () => {
  it("подставляет символ валюты", () => {
    expect(formatCompactCurrency(4_809_747_848.94)).toBe("4,81 млрд ₸");
    expect(formatCompactCurrency(1_500_000, "USD")).toBe("1,5 млн $");
  });
});
