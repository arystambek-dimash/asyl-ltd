import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { finiteMoney } from "@/lib/currency-map";
import type { ClientDebt } from "@/lib/types";

const MONEY_FORMATTER = new Intl.NumberFormat("ru-RU", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2,
});
const COMPACT_FORMATTERS = [0, 1, 2].map(
  (maximumFractionDigits) =>
    new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: 0,
      maximumFractionDigits,
    }),
);
const COUNT_FORMATTER = new Intl.NumberFormat("ru-RU");
const ISO_DATE_FORMATTER = new Intl.DateTimeFormat("ru-RU");
const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});
const TIME_FORMATTER = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * 16px у полей в окнах на весь экран телефона: iOS зумит страницу при фокусе
 * на поле мельче 16px. С `sm` — обычный размер.
 */
export const PHONE_INPUT_TEXT = "text-base sm:text-sm";

export function formatMoney(value: number | string): string {
  return MONEY_FORMATTER.format(finiteMoney(value));
}

/** Штуки (мешки, вагоны) с разрядами: 12 345 → «12 345». Не деньги — formatMoney сюда не подставлять. */
export function formatCount(value: number): string {
  return COUNT_FORMATTER.format(value);
}

/** Килограммы в тоннах для людей, до 2 знаков: 67 555 → «67,56», 68 000 → «68». Единицу «т» дописывает вызывающий. */
export function formatTons(kg: number | string): string {
  return formatMoney(Number(kg) / 1000);
}

/** Крупное число одним взглядом: 4 809 747 848 → «4,81 млрд».
 *
 * На дашборде важен порядок величины, а не копейки: полное число из
 * двенадцати цифр читается дольше, чем занимает вся карточка. Точное
 * значение остаётся в подсказке — см. formatMoney.
 */
export function formatCompact(value: number | string): string {
  const n = finiteMoney(value);
  const abs = Math.abs(n);
  // Ниже 100 000 сокращать нечего: «99 999» и короче, и точнее «100,0 тыс».
  if (abs < 100_000) return formatMoney(n);
  const [divisor, suffix] =
    abs >= 1_000_000_000 ? [1_000_000_000, " млрд"] : abs >= 1_000_000 ? [1_000_000, " млн"] : [1_000, " тыс"];
  const scaled = n / divisor;
  // Две значащие цифры после запятой у единиц, одна — у десятков и выше.
  const digits = Math.abs(scaled) < 10 ? 2 : 1;
  const text = COMPACT_FORMATTERS[digits].format(scaled);
  // Неразрывный пробел перед «млн»: в узкой колонке обычный рвал число на
  // две строки — «100» сверху, «млн» снизу.
  return `${text} ${suffix.trim()}`;
}

/** Долги по валютам. Складывать KZT и USD нельзя — 1000 ₸ и 5 $ не дают 1005.
 *
 * Суммируется разбивка debt_by_currency: плоское debt_total описывает только
 * основную валюту клиента. Правило одно на всю систему, поэтому живёт здесь,
 * а не копией на каждой странице.
 */
export function sumDebtByCurrency(rows: readonly Pick<ClientDebt, "debt_by_currency">[]): Record<string, number> {
  return rows.reduce<Record<string, number>>((totals, row) => {
    for (const [currency, amount] of Object.entries(row.debt_by_currency)) {
      totals[currency] = (totals[currency] ?? 0) + finiteMoney(amount);
    }
    return totals;
  }, {});
}

/** Складывает денежные строки только внутри одной валюты. */
export function sumMoneyByCurrency<T>(
  rows: readonly T[],
  amountOf: (row: T) => number | string | null | undefined,
  currencyOf: (row: T) => string | null | undefined,
): Record<string, number> {
  return rows.reduce<Record<string, number>>((totals, row) => {
    const currency = currencyOf(row) || "KZT";
    totals[currency] = (totals[currency] ?? 0) + finiteMoney(amountOf(row));
    return totals;
  }, {});
}

export function currencySymbol(currency: "KZT" | "USD" | string = "KZT"): string {
  if (currency === "KZT") return "₸";
  if (currency === "USD") return "$";
  return currency;
}

export function formatCompactCurrency(value: number | string, currency: "KZT" | "USD" | string = "KZT"): string {
  return `${formatCompact(value)} ${currencySymbol(currency)}`;
}

export function formatCurrency(value: number | string, currency: "KZT" | "USD" | string = "KZT"): string {
  return `${formatMoney(value)} ${currencySymbol(currency)}`;
}

/** Номер операции «PAY-000123» — тот же формат, что `PAY-{pk:06d}` на сервере (поиск ленты его разбирает). */
export function formatPaymentNumber(id: number | string): string {
  return `PAY-${String(id).padStart(6, "0")}`;
}

export function formatPortalMoney(value: string | null | undefined, currency: "KZT" | "USD" | string = "KZT"): string {
  return value == null ? "После подтверждения" : formatCurrency(value, currency);
}

/** Calendar date in local time; unlike toISOString(), does not shift by UTC. */
export function toLocalIsoDate(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function todayLocalIsoDate(): string {
  return toLocalIsoDate(new Date());
}

export function monthStartLocalIsoDate(now = new Date()): string {
  return toLocalIsoDate(new Date(now.getFullYear(), now.getMonth(), 1));
}

/** Календарная дата «ГГГГ-ММ-ДД», сдвинутая на `days` дней (отрицательные — назад). */
export function shiftIsoDate(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  return toLocalIsoDate(new Date(year, month - 1, day + days));
}

/** Ошибка периода «с — по» (даты «ГГГГ-ММ-ДД»); null — период корректен или открыт с одной стороны. */
export function dateRangeError(from: string, to: string): string | null {
  return from && to && from > to ? "Дата начала не может быть позже даты окончания." : null;
}

/** Период аналитики камер «с — по» (даты «ГГГГ-ММ-ДД»): дней включительно и укладывается ли он в 1–366 дней, как требует API. */
export function analyticsRange(from: string, to: string): { days: number; valid: boolean } {
  const days = (Date.parse(to) - Date.parse(from)) / 86_400_000 + 1;
  return { days, valid: Number.isFinite(days) && days >= 1 && days <= 366 };
}

/** Адрес API с параметрами фильтров: пустые и «all» (без фильтра) в запрос не попадают. */
export function apiUrl(path: string, params: Record<string, string>) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value && value !== "all") query.set(key, value);
  });
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

/** Текст ошибки загрузки useApi. На 403 apiError пуст (об отказе сообщает всплывашка),
 * но экран без данных тоже должен сказать, почему пуст, — тогда показываем fallback. */
export function loadErrorText(state: { error: string; errorStatus: number | null }, fallback: string): string {
  return state.error || (state.errorStatus ? fallback : "");
}

/** Новый Set с переключённым элементом: был — убран, не было — добавлен. */
export function toggledSet<T>(set: ReadonlySet<T>, item: T): Set<T> {
  const next = new Set(set);
  if (next.has(item)) next.delete(item);
  else next.add(item);
  return next;
}

/** Календарная дата «ГГГГ-ММ-ДД» по-русски: «28.07.2026».
 *
 * `new Date("2026-07-28")` разбирается как UTC-полночь, и восточнее Гринвича
 * (Алматы, +5) `toLocaleDateString` показал бы предыдущий день. Здесь дата
 * читается как локальная, без сдвига.
 */
export function formatIsoDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  if (!year || !month || !day) return value;
  return ISO_DATE_FORMATTER.format(new Date(year, month - 1, day));
}

/** Календарная дата «ГГГГ-ММ-ДД» коротко: «28.07» — подписи столбиков и плашек дня. */
export function formatIsoDayMonth(value: string): string {
  const [, month, day] = value.split("-");
  return month && day ? `${day}.${month}` : value;
}

/** Дата и время по-русски: «13.07.2026, 14:32». */
export function formatDateTime(value: string | Date): string {
  return DATE_TIME_FORMATTER.format(new Date(value));
}

/** Время по-русски: «14:32». */
export function formatTime(value: string | Date): string {
  return TIME_FORMATTER.format(new Date(value));
}

/** Русская форма слова по числу: pluralRu(3, ["вагон", "вагона", "вагонов"]) → «вагона». */
export function pluralRu(count: number, forms: [string, string, string]): string {
  const n = Math.abs(count) % 100;
  const last = n % 10;
  if (n > 10 && n < 20) return forms[2];
  if (last === 1) return forms[0];
  if (last >= 2 && last <= 4) return forms[1];
  return forms[2];
}

/** «мешок/мешка/мешков» по числу. */
export const bagsWord = (count: number) => pluralRu(count, ["мешок", "мешка", "мешков"]);

/** «3 мешка». */
export const bagsLabel = (count: number) => `${count} ${bagsWord(count)}`;
