import { describe, expect, it } from "vitest";

import { findCountry } from "./countries";
import {
  composePhone,
  isKaspiInvoicePhone,
  isPhoneComplete,
  maskHint,
  missingPhoneDigits,
  parsePhone,
  readPhoneInput,
} from "./phone";

const KZ = findCountry("Казахстан");
const RU = findCountry("Россия");
const UZ = findCountry("Узбекистан");
const KG = findCountry("Кыргызстан");

const typed = (raw: string, country = KZ) => composePhone(readPhoneInput(raw, country));

describe("readPhoneInput", () => {
  it("оформляет казахстанский номер по маске", () => {
    expect(typed("7055656565")).toBe("+7 (705) 565-65-65");
    expect(typed("705")).toBe("+7 (705");
  });

  it("понимает привычные 8… и 7… перед номером", () => {
    expect(typed("87055656565")).toBe("+7 (705) 565-65-65");
    expect(typed("77055656565")).toBe("+7 (705) 565-65-65");
    expect(typed("8")).toBe("");
  });

  it("переключает страну при вставке номера с кодом", () => {
    expect(readPhoneInput("+998 90 123 45 67", KZ)).toEqual({ country: UZ, digits: "901234567" });
    expect(readPhoneInput("998901234567", KZ)).toEqual({ country: UZ, digits: "901234567" });
  });

  it("при общем коде +7 оставляет выбранную Россию", () => {
    expect(readPhoneInput("+7 912 123-45-67", RU)).toEqual({ country: RU, digits: "9121234567" });
    expect(readPhoneInput("89121234567", RU)).toEqual({ country: RU, digits: "9121234567" });
  });

  it("срезает 0 перед местным номером", () => {
    expect(typed("0555123456", KG)).toBe("+996 555 123-456");
  });

  it("не даёт набрать лишние цифры", () => {
    expect(typed("90123456789", UZ)).toBe("+998 90 123-45-67");
  });

  it("для другой страны хранит номер целиком", () => {
    expect(readPhoneInput("+49 151 2345 6789", null)).toEqual({ country: null, digits: "4915123456789" });
    expect(readPhoneInput("+7705", null)).toEqual({ country: KZ, digits: "705" });
  });
});

describe("parsePhone", () => {
  it("разбирает сохранённые номера, включая старые без +", () => {
    expect(parsePhone("+998 90 123-45-67")).toEqual({ country: UZ, digits: "901234567" });
    expect(parsePhone("87055656565")).toEqual({ country: KZ, digits: "7055656565" });
    expect(parsePhone("", RU)).toEqual({ country: RU, digits: "" });
  });
});

describe("полнота номера", () => {
  it("считает недостающие цифры", () => {
    expect(missingPhoneDigits("+7 (705) 565")).toBe(4);
    expect(isPhoneComplete("+7 (705) 565-65-65")).toBe(true);
    expect(isPhoneComplete("+998 90 123-45")).toBe(false);
    expect(isPhoneComplete("+4915123456789")).toBe(true);
    expect(isPhoneComplete("")).toBe(false);
  });

  it("принимает для счёта Kaspi номер так же, как normalize_phone на сервере", () => {
    expect(isKaspiInvoicePhone("8 700 000 00 00")).toBe(true);
    expect(isKaspiInvoicePhone("+7 (705) 565-65-65")).toBe(true);
    expect(isKaspiInvoicePhone("700 000 00 00")).toBe(true);
    expect(isKaspiInvoicePhone("97001234567")).toBe(false);
    expect(isKaspiInvoicePhone("+7 700 000 00")).toBe(false);
    expect(isKaspiInvoicePhone("")).toBe(false);
  });

  it("подсказывает хвост маски", () => {
    expect(maskHint("(###) ###-##-##", 0)).toBe("(___) ___-__-__");
    expect(maskHint("(###) ###-##-##", 3)).toBe(") ___-__-__");
    expect(maskHint("(###) ###-##-##", 4)).toBe("__-__-__");
  });
});
