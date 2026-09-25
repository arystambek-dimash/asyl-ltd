import { WEEKDAY_NAMES } from "@/lib/calendar-month";
import type { Store } from "@/lib/types";

type PaymentScheduleType = Store["payment_schedule_type"];

type PaymentScheduleValidation = { ok: true; days: number[] } | { ok: false; message: string };

interface PaymentSchedule {
  payment_schedule_type: PaymentScheduleType;
  payment_days?: readonly number[] | null;
}

type PaymentScheduleFormat = "list" | "detail" | "payment";

function uniqueSorted(values: number[]) {
  return [...new Set(values)].sort((left, right) => left - right);
}

/** Дни расписания без мусора: целые в пределах месяца или недели, по возрастанию, без повторов. */
export function normalizedScheduleDays(schedule: PaymentSchedule): number[] {
  const maxDay = schedule.payment_schedule_type === "weekly" ? 7 : 31;
  return uniqueSorted(
    (schedule.payment_days ?? []).filter((day) => Number.isInteger(day) && day >= 1 && day <= maxDay),
  );
}

export function formatPaymentSchedule(schedule: PaymentSchedule, format: PaymentScheduleFormat = "list"): string {
  const { payment_schedule_type: scheduleType } = schedule;
  if (scheduleType === "none") {
    if (format === "detail") return "Без расписания — оплата в любой день";
    return format === "payment" ? "Свободная оплата" : "—";
  }

  const days = normalizedScheduleDays(schedule);
  if (scheduleType === "monthly") {
    if (days.length === 0) return "Числа не заданы";
    const prefix = format === "detail" ? "Оплата по числам месяца: " : "Числа: ";
    return `${prefix}${days.join(", ")}`;
  }

  if (days.length === 0) return "Дни не заданы";
  const prefix = format === "detail" ? "Оплата по дням недели: " : "Дни: ";
  return `${prefix}${days.map((day) => WEEKDAY_NAMES[day - 1]).join(", ")}`;
}

export function validatePaymentSchedule(
  scheduleType: PaymentScheduleType,
  monthlyInput: string,
  weeklyDays: number[],
): PaymentScheduleValidation {
  if (scheduleType === "none") return { ok: true, days: [] };

  if (scheduleType === "weekly") {
    if (weeklyDays.length === 0) {
      return { ok: false, message: "Выберите хотя бы один день недели." };
    }
    if (weeklyDays.some((day) => !Number.isInteger(day) || day < 1 || day > 7)) {
      return { ok: false, message: "Дни недели должны быть от понедельника до воскресенья." };
    }
    return { ok: true, days: uniqueSorted(weeklyDays) };
  }

  const tokens = monthlyInput.trim() ? monthlyInput.trim().split(/[,\s]+/) : [];
  if (tokens.length === 0) {
    return { ok: false, message: "Укажите хотя бы одно число месяца." };
  }
  if (tokens.some((token) => !/^\d{1,2}$/.test(token))) {
    return { ok: false, message: "Введите целые числа от 1 до 31 через запятую." };
  }

  const days = tokens.map(Number);
  if (days.some((day) => day < 1 || day > 31)) {
    return { ok: false, message: "Числа оплаты должны быть от 1 до 31." };
  }
  return { ok: true, days: uniqueSorted(days) };
}
