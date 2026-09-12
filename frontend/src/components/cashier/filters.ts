import { toLocalIsoDate } from "@/lib/utils";
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
export type FilterScreen = "overview" | "report" | "debts" | "confirm" | "journal";
export type CashFiltersByScreen = Record<FilterScreen, CashFilters>;

export type PeriodPreset = "today" | "week" | "month" | "all";
export const PERIOD_PRESETS: { key: PeriodPreset; label: string }[] = [
  { key: "today", label: "Сегодня" },
  { key: "week", label: "Неделя" },
  { key: "month", label: "Месяц" },
  { key: "all", label: "Всё" },
];

/** Границы пресета: неделя — последние 7 дней включая сегодня, месяц — с 1-го числа по сегодня. */
export function periodRange(preset: PeriodPreset, now = new Date()): Pick<CashFilters, "dateFrom" | "dateTo"> {
  const today = toLocalIsoDate(now);
  switch (preset) {
    case "today":
      return { dateFrom: today, dateTo: today };
    case "week":
      return {
        dateFrom: toLocalIsoDate(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 6)),
        dateTo: today,
      };
    case "month":
      return { dateFrom: toLocalIsoDate(new Date(now.getFullYear(), now.getMonth(), 1)), dateTo: today };
    case "all":
      return { dateFrom: "", dateTo: "" };
  }
}

export function periodPresetOf(
  filters: Pick<CashFilters, "dateFrom" | "dateTo">,
  now = new Date(),
): PeriodPreset | "custom" {
  const preset = PERIOD_PRESETS.find(({ key }) => {
    const range = periodRange(key, now);
    return range.dateFrom === filters.dateFrom && range.dateTo === filters.dateTo;
  });
  return preset?.key ?? "custom";
}

/** Отчёт на телефоне открывается за сегодня, как у Kaspi; остальные экраны без ограничений. */
export function initialFilters(now = new Date()): CashFiltersByScreen {
  return {
    overview: EMPTY_CASH_FILTERS,
    report: { ...EMPTY_CASH_FILTERS, ...periodRange("today", now) },
    debts: EMPTY_CASH_FILTERS,
    confirm: EMPTY_CASH_FILTERS,
    journal: EMPTY_CASH_FILTERS,
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
    case "journal":
      return view;
    default:
      return null;
  }
}

export function apiUrl(path: string, params: Record<string, string>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value && value !== "all") query.set(key, value);
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export function filtersAreValid(filters: CashFilters) {
  const datesOk = !filters.dateFrom || !filters.dateTo || filters.dateFrom <= filters.dateTo;
  const min = filters.remainingMin === "" ? null : Number(filters.remainingMin);
  const max = filters.remainingMax === "" ? null : Number(filters.remainingMax);
  const remainingOk = min === null || max === null || min <= max;
  return datesOk && remainingOk;
}

/** Текст ошибки диапазона для панели/шторки/экрана; null — фильтры корректны. */
export function filtersError(filters: CashFilters): string | null {
  if (filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo) {
    return "Дата начала не может быть позже даты окончания.";
  }
  if (filters.remainingMin && filters.remainingMax && Number(filters.remainingMin) > Number(filters.remainingMax)) {
    return "Минимальный остаток не может быть больше максимального.";
  }
  return null;
}

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
