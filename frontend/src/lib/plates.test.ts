import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import {
  detectPlateCountry,
  formatOcrConfidence,
  formatPlate,
  formatPlatePair,
  isValidPlate,
  isValidWagonNumber,
  normalizePlate,
  plateCountryFor,
  plateCountryIso,
  plateDisplay,
  plateMatchKey,
  plateWarning,
  readPlateInput,
  sameTransportPair,
  transportBody,
  transportChanges,
  transportNumberError,
  transportPairOf,
  typedWagonNumber,
} from "./plates";

// Те же векторы проверяет бэкенд (apps/common/plates.py): правила должны совпадать.
// Файл читается во время теста, а не импортом: сборка фронта идёт без каталога backend.
// Путь — от самого файла теста: запуск не из каталога frontend его не ломает.
const VECTORS = JSON.parse(
  readFileSync(resolve(import.meta.dirname, "../../../backend/apps/common/tests/plate_vectors.json"), "utf-8"),
) as {
  normalize: [string, string][];
  match_key: [string, string][];
  country: [string, string | null][];
  format: [string, string][];
  valid: string[];
  invalid: string[];
  truck_warning: [string, boolean][];
  trailer_warning: [string, boolean][];
};

describe("правила номеров — общие векторы с бэкендом", () => {
  it.each(VECTORS.normalize)("normalizePlate(%j) → %j", (raw, expected) => {
    expect(normalizePlate(raw)).toBe(expected);
  });

  it.each(VECTORS.match_key)("plateMatchKey(%j) → %j", (compact, expected) => {
    expect(plateMatchKey(compact)).toBe(expected);
  });

  it.each(VECTORS.country)("detectPlateCountry(%j) → %j", (compact, expected) => {
    expect(detectPlateCountry(compact)).toBe(expected);
  });

  it.each(VECTORS.format)("formatPlate(%j) → %j", (raw, expected) => {
    expect(formatPlate(raw)).toBe(expected);
  });

  it.each(VECTORS.valid)("%j — номер", (raw) => {
    expect(isValidPlate(raw)).toBe(true);
  });

  it.each(VECTORS.invalid)("%j — не номер", (raw) => {
    expect(isValidPlate(raw)).toBe(false);
  });

  it.each(VECTORS.truck_warning)("тягач %j: предупреждение — %j", (compact, warned) => {
    expect(plateWarning(compact) !== null).toBe(warned);
  });

  it.each(VECTORS.trailer_warning)("прицеп %j: предупреждение — %j", (compact, warned) => {
    expect(plateWarning(compact, "trailer") !== null).toBe(warned);
  });
});

describe("formatPlatePair", () => {
  it("пишет прицеп через косую черту", () => {
    expect(formatPlatePair("07KG695ADT", "07KG837PB")).toBe("07 KG 695 ADT / 07 KG 837 PB");
    expect(formatPlatePair("403BJN13", "")).toBe("403 BJN 13");
    expect(formatPlatePair("", "07KG837PB")).toBe("— / 07 KG 837 PB");
    expect(formatPlatePair("", "")).toBe("");
  });
});

describe("ввод номера", () => {
  it("оставляет только латиницу и цифры, кириллицу-двойники переводит", () => {
    expect(readPlateInput("403 вjн 13")).toBe("403BJH13");
    expect(readPlateInput("07/kg_695")).toBe("07KG695");
    expect(readPlateInput("А123ВС77")).toBe("A123BC77");
  });

  it("не даёт набрать больше 12 знаков", () => {
    expect(readPlateInput("1234567890ABCDEF")).toBe("1234567890AB");
  });

  it("показывает набранное по маске страны и серый хвост", () => {
    expect(plateDisplay("", "KZ")).toEqual({ text: "", hint: "000 AAA 00" });
    expect(plateDisplay("403", "KZ")).toEqual({ text: "403", hint: " AAA 00" });
    expect(plateDisplay("403B", "KZ")).toEqual({ text: "403 B", hint: "AA 00" });
    expect(plateDisplay("403BJN13", "KZ")).toEqual({ text: "403 BJN 13", hint: "" });
    expect(plateDisplay("07", "KG")).toEqual({ text: "07", hint: " KG 000 AAA" });
    expect(plateDisplay("07KG695ADT", "KG")).toEqual({ text: "07 KG 695 ADT", hint: "" });
  });

  it("у прицепа своя маска", () => {
    expect(plateDisplay("", "KZ", "trailer")).toEqual({ text: "", hint: "000 AA 00" });
    expect(plateDisplay("07KG837", "KG", "trailer")).toEqual({ text: "07 KG 837", hint: " AA" });
  });

  it("у «Другой» страны маски нет", () => {
    expect(plateDisplay("", null)).toEqual({ text: "", hint: "" });
    expect(plateDisplay("AB12345", null)).toEqual({ text: "AB12345", hint: "" });
  });

  it("номер не по маске выбранной страны выводится как есть", () => {
    expect(plateDisplay("CLIENT777", "KZ")).toEqual({ text: "CLIENT777", hint: "" });
    expect(plateDisplay("07KG695ADT", "KZ")).toEqual({ text: "07 KG 695 ADT", hint: "" });
  });

  it("страна поля следует за набранным номером", () => {
    // Полный номер известной страны.
    expect(plateCountryFor("07KG695ADT", "KZ")).toBe("KG");
    // Начало подходит под выбранную страну — она остаётся.
    expect(plateCountryFor("0", "UZ")).toBe("UZ");
    // Начало не подходит под выбранную — первая страна, под чью маску подходит.
    expect(plateCountryFor("01A", "KZ")).toBe("UZ");
    // Неоднозначный полный номер (KG без «KG» или юрлицо UZ) — выбор остаётся.
    expect(plateCountryFor("01123ABC", "UZ")).toBe("UZ");
    // «Другая» переключается только на полный номер известной страны.
    expect(plateCountryFor("4", null)).toBeNull();
    expect(plateCountryFor("403BJN13", null)).toBe("KZ");
    // Ни под одну маску — выбор остаётся.
    expect(plateCountryFor("CLIENT777", "KZ")).toBe("KZ");
  });

  it("переводит страну клиента в страну номера", () => {
    expect(plateCountryIso("Кыргызстан")).toBe("KG");
    expect(plateCountryIso("Россия")).toBe("RU");
    // Номера стран вне списка — свободного формата.
    expect(plateCountryIso("Таджикистан")).toBeNull();
    expect(plateCountryIso("Другая")).toBeNull();
    // Страна не указана — Казахстан.
    expect(plateCountryIso("")).toBe("KZ");
    expect(plateCountryIso(undefined)).toBe("KZ");
  });
});

describe("пара номеров", () => {
  it("сравнивает с точностью до записи", () => {
    const saved = { truck_number: "07KG695ADT", trailer_number: "" };
    expect(sameTransportPair(saved, { truck_number: "07 695 adt", trailer_number: " " })).toBe(true);
    expect(sameTransportPair(saved, { truck_number: "07KG695ADT", trailer_number: "07KG837PB" })).toBe(false);
  });

  it("берёт пару из заказа: у старых заказов прицепа нет", () => {
    expect(transportPairOf({ truck_number: "403BJN13" })).toEqual({ truck_number: "403BJN13", trailer_number: "" });
    expect(transportPairOf({ truck_number: null, trailer_number: "07KG837PB" })).toEqual({
      truck_number: "",
      trailer_number: "07KG837PB",
    });
  });

  it("отправляет номера слитно, у вагона — только номер вагона", () => {
    const pair = { truck_number: "403 bjn 13", trailer_number: "" };
    expect(transportBody("truck", pair)).toEqual({ truck_number: "403BJN13", trailer_number: "" });
    expect(transportBody("train", { truck_number: "0012 3456", trailer_number: "" })).toEqual({
      truck_number: "00123456",
    });
  });

  it("отправляет только исправленные номера: пустой — стереть, прежний — не отправлять", () => {
    const saved = { truck_number: "07KG695ADT", trailer_number: "07KG837PB" };
    // Другая запись того же номера — не правка.
    expect(transportChanges(saved, { truck_number: "07 695 adt", trailer_number: "07kg837pb" })).toEqual({});
    expect(transportChanges(saved, { truck_number: "07 kg 695 adt", trailer_number: "" })).toEqual({
      trailer_number: "",
    });
    expect(transportChanges(saved, { truck_number: "403 bjn 13", trailer_number: "07KG837PB" })).toEqual({
      truck_number: "403BJN13",
    });
  });
});

describe("номер, который API не примет", () => {
  it("номер вагона — 8 цифр, оформление не мешает", () => {
    expect(isValidWagonNumber("12345678")).toBe(true);
    expect(isValidWagonNumber(" 1234 5678 ")).toBe(true);
    expect(isValidWagonNumber("1234567")).toBe(false);
    expect(isValidWagonNumber("1234567A")).toBe(false);
  });

  it("при наборе номера вагона оформление отбрасывается, лишние знаки не вводятся", () => {
    expect(typedWagonNumber("0012 3456")).toBe("00123456");
    expect(typedWagonNumber("0012-34567")).toBe("00123456");
    expect(typedWagonNumber("1234")).toBe("1234");
  });

  it("объясняет, почему номер не примут; пустой — «номера нет»", () => {
    expect(transportNumberError("", "truck")).toBeNull();
    expect(transportNumberError("07kg837pb", "truck")).toBeNull();
    expect(transportNumberError("12", "truck")).toBe("Номер: от 4 до 12 латинских букв и цифр");
    expect(transportNumberError("1234", "train")).toBe("Номер вагона: 8 цифр");
    expect(transportNumberError("1234 5678", "train")).toBeNull();
  });
});

describe("уверенность OCR", () => {
  it("доля 0…1 и готовые проценты дают один и тот же вид", () => {
    expect(formatOcrConfidence(0.873)).toBe("87%");
    expect(formatOcrConfidence("0.9500")).toBe("95%");
    expect(formatOcrConfidence(87.3)).toBe("87%");
  });

  it("пустое значение — прочерк, а не 0%", () => {
    expect(formatOcrConfidence(null)).toBe("—");
    expect(formatOcrConfidence("")).toBe("—");
    expect(formatOcrConfidence("abc")).toBe("—");
  });
});
