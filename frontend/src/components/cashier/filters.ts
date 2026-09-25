import { periodRange, type PeriodOption } from "@/lib/date-range";
import { dateRangeError, todayLocalIsoDate } from "@/lib/utils";
import type { CashView } from "./view";

export interface CashFilters {
  dateFrom: string;
  dateTo: string;
  department: string;
  store: string;
  remainingMin: string;
  remainingMax: string;
  remainingCurrency: string;
}

export const EMPTY_CASH_FILTERS: CashFilters = {
  dateFrom: "",
  dateTo: "",
  department: "all",
  store: "all",
  remainingMin: "",
  remainingMax: "",
  remainingCurrency: "all",
};

/** Свои фильтры у каждого экрана: период журнала не должен обрезать «Общее», остаток долга нужен только долгам. */
type FilterScreen = "overview" | "report" | "debts" | "confirm";
export type CashFiltersByScreen = Record<FilterScreen, CashFilters>;

export const PERIOD_PRESETS: PeriodOption<"today" | "week" | "month" | "all">[] = [
  { key: "today", label: "Сегодня" },
  { key: "week", label: "Неделя" },
  { key: "month", label: "Месяц" },
  { key: "all", label: "Всё" },
];

/** Отчёт на телефоне открывается за сегодня, как у Kaspi; остальные экраны без ограничений. */
export function initialFilters(today = todayLocalIsoDate()): CashFiltersByScreen {
  return {
    overview: EMPTY_CASH_FILTERS,
    report: { ...EMPTY_CASH_FILTERS, ...periodRange("today", today) },
    debts: EMPTY_CASH_FILTERS,
    confirm: EMPTY_CASH_FILTERS,
  };
}

/** Экран фильтров для view; null — у экрана нет панели фильтров (у транзакций свой поиск). */
export function filterScreenFor(view: CashView, mobile: boolean): FilterScreen | null {
  switch (view) {
    case "overview":
      return mobile ? null : "overview";
    case "report":
    case "debts":
      return mobile ? view : null;
    case "confirm":
      return view;
    default:
      return null;
  }
}

/** Текст ошибки диапазона для панели/шторки/экрана; null — фильтры корректны. */
export function filtersError(filters: CashFilters): string | null {
  const rangeError = dateRangeError(filters.dateFrom, filters.dateTo);
  if (rangeError) return rangeError;
  if (filters.remainingMin && filters.remainingMax && Number(filters.remainingMin) > Number(filters.remainingMax)) {
    return "Минимальный остаток не может быть больше максимального.";
  }
  return null;
}

export const filtersAreValid = (filters: CashFilters) => filtersError(filters) === null;

/** Сколько групп фильтров задано — для подписи «Применено: N» и бейджа на иконке. */
export function activeFilterCount(
  filters: CashFilters,
  {
    dates = true,
    remaining = false,
    department = true,
  }: { dates?: boolean; remaining?: boolean; department?: boolean } = {},
): number {
  return [
    dates && (filters.dateFrom !== "" || filters.dateTo !== ""),
    department && filters.department !== "all",
    filters.store !== "all",
    remaining && (filters.remainingMin !== "" || filters.remainingMax !== ""),
  ].filter(Boolean).length;
}

/** Общие параметры очереди/журнала/долгов: даты, отдел, магазин. */
export function scopeParams(filters: CashFilters) {
  return {
    date_from: filters.dateFrom,
    date_to: filters.dateTo,
    department: filters.department,
    store: filters.store,
  };
}
