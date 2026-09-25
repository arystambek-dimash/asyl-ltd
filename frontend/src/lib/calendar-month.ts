/** Сетка месяца для календаря: недели с понедельника, полные ряды по 7 дней. */
import { toLocalIsoDate } from "@/lib/utils";

const MONTH_NAMES = [
  "Январь",
  "Февраль",
  "Март",
  "Апрель",
  "Май",
  "Июнь",
  "Июль",
  "Август",
  "Сентябрь",
  "Октябрь",
  "Ноябрь",
  "Декабрь",
];

/** Родительный падеж для подписи дня: «6 сентября». */
export const MONTH_NAMES_OF = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

export const WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

/** «ГГГГ-ММ» месяца, которому принадлежит день. */
export function monthOf(iso: string): string {
  return iso.slice(0, 7);
}

export function monthTitle(month: string): string {
  const [year, index] = month.split("-").map(Number);
  return `${MONTH_NAMES[index - 1]} ${year}`;
}

/** Соседний месяц: shiftMonth("2026-01", -1) → "2025-12". */
export function shiftMonth(month: string, delta: number): string {
  const [year, index] = month.split("-").map(Number);
  const shifted = new Date(year, index - 1 + delta, 1);
  return toLocalIsoDate(shifted).slice(0, 7);
}

interface MonthDay {
  iso: string;
  day: number;
  /** День соседнего месяца, добивающий неделю. */
  outside: boolean;
}

export function monthGrid(month: string): MonthDay[] {
  const [year, index] = month.split("-").map(Number);
  const first = new Date(year, index - 1, 1);
  // getDay(): воскресенье — 0, а неделя начинается с понедельника.
  const lead = (first.getDay() + 6) % 7;
  const start = new Date(year, index - 1, 1 - lead);
  const days: MonthDay[] = [];
  for (let offset = 0; offset < 42; offset++) {
    const current = new Date(start.getFullYear(), start.getMonth(), start.getDate() + offset);
    const iso = toLocalIsoDate(current);
    days.push({ iso, day: current.getDate(), outside: monthOf(iso) !== month });
    // Шестая неделя нужна не всегда: обрываем, когда месяц уже закончился.
    if (offset >= 34 && offset % 7 === 6 && monthOf(iso) !== month) break;
  }
  return days;
}
