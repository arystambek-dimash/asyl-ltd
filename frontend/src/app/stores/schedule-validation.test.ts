import { describe, expect, it } from "vitest";
import { formatPaymentSchedule, validatePaymentSchedule } from "./schedule-validation";

describe("formatPaymentSchedule", () => {
  it("uses the requested unrestricted-schedule wording", () => {
    const schedule = { payment_schedule_type: "none", payment_days: [1, 3] } as const;

    expect(formatPaymentSchedule(schedule)).toBe("—");
    expect(formatPaymentSchedule(schedule, "detail")).toBe("Без расписания — оплата в любой день");
    expect(formatPaymentSchedule(schedule, "payment")).toBe("Свободная оплата");
  });

  it("formats and normalizes monthly days", () => {
    const schedule = { payment_schedule_type: "monthly", payment_days: [20, 5, 20, 1] } as const;

    expect(formatPaymentSchedule(schedule)).toBe("Числа: 1, 5, 20");
    expect(formatPaymentSchedule(schedule, "detail")).toBe("Оплата по числам месяца: 1, 5, 20");
  });

  it("formats and normalizes weekly days", () => {
    const schedule = { payment_schedule_type: "weekly", payment_days: [5, 1, 5] } as const;

    expect(formatPaymentSchedule(schedule)).toBe("Дни: Пн, Пт");
    expect(formatPaymentSchedule(schedule, "detail")).toBe("Оплата по дням недели: Пн, Пт");
  });

  it("ignores invalid days and uses the empty-state copy when none remain", () => {
    expect(formatPaymentSchedule({ payment_schedule_type: "monthly", payment_days: [0, 32, Number.NaN] })).toBe(
      "Числа не заданы",
    );
    expect(formatPaymentSchedule({ payment_schedule_type: "weekly", payment_days: [0, 8, 1.5] })).toBe("Дни не заданы");
  });
});

describe("validatePaymentSchedule", () => {
  it("keeps an unrestricted schedule empty", () => {
    expect(validatePaymentSchedule("none", "5, 20", [1, 3])).toEqual({ ok: true, days: [] });
  });

  it("normalizes valid monthly days", () => {
    expect(validatePaymentSchedule("monthly", "20, 5 20, 01", [])).toEqual({
      ok: true,
      days: [1, 5, 20],
    });
  });

  it.each([
    ["", "Укажите хотя бы одно число месяца."],
    ["5, завтра", "Введите целые числа от 1 до 31 через запятую."],
    ["0, 15", "Числа оплаты должны быть от 1 до 31."],
    ["1, 32", "Числа оплаты должны быть от 1 до 31."],
  ])("rejects invalid monthly input %j", (input, message) => {
    expect(validatePaymentSchedule("monthly", input, [])).toEqual({ ok: false, message });
  });

  it("requires at least one weekly day", () => {
    expect(validatePaymentSchedule("weekly", "", [])).toEqual({
      ok: false,
      message: "Выберите хотя бы один день недели.",
    });
  });

  it("normalizes valid weekdays and rejects corrupted values", () => {
    expect(validatePaymentSchedule("weekly", "", [5, 1, 5])).toEqual({ ok: true, days: [1, 5] });
    expect(validatePaymentSchedule("weekly", "", [1, 8])).toEqual({
      ok: false,
      message: "Дни недели должны быть от понедельника до воскресенья.",
    });
  });
});
