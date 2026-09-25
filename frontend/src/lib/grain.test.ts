import { describe, expect, it } from "vitest";
import { grainTripStepIndex, grainTripSteps, isManualReasonValid, passageNetKg, siloAcceptsType } from "@/lib/grain";

describe("grainTripStepIndex", () => {
  it("одна модель шагов для таблицы и карточки рейса", () => {
    expect(grainTripStepIndex("expected")).toBe(0);
    expect(grainTripStepIndex("arrived")).toBe(1);
    expect(grainTripStepIndex("at_silo")).toBe(2);
    expect(grainTripStepIndex("gross_weighed")).toBe(2);
    // Расхождение возникает после выходного взвешивания — это последний шаг, а не силос.
    expect(grainTripStepIndex("weight_discrepancy")).toBe(3);
    expect(grainTripStepIndex("completed")).toBe(4);
  });

  it("неизвестный статус легаси-потока прижимается к силосному этапу", () => {
    expect(grainTripStepIndex("something_new")).toBe(2);
  });

  it("у прихода и вывоза по 4 своих шага", () => {
    expect(grainTripSteps("intake").map((step) => step.label)).toEqual([
      "Заезд",
      "Входные весы (позже)",
      "Разгрузка",
      "Выходные весы (позже)",
    ]);
    expect(grainTripSteps("passage").map((step) => step.label)).toEqual([
      "Заезд",
      "Весы пустого",
      "Погрузка",
      "Весы гружёного",
    ]);
  });
});

describe("siloAcceptsType", () => {
  it("силос без типа принимает любой тип, с типом — только свой", () => {
    expect(siloAcceptsType({ silo_type: null }, 2)).toBe(true);
    expect(siloAcceptsType({ silo_type: 2 }, 2)).toBe(true);
    expect(siloAcceptsType({ silo_type: 1 }, 2)).toBe(false);
  });
});

describe("ручное взвешивание", () => {
  it("причина — не короче 5 символов без пробелов по краям", () => {
    expect(isManualReasonValid("  ошиб ")).toBe(false);
    expect(isManualReasonValid("ошибка")).toBe(true);
  });

  it("нетто вывоза в превью — выезд минус въезд", () => {
    expect(passageNetKg(41_000, 15_000)).toBe(26_000);
  });
});
