import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

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
const ISO_DATE_FORMATTER = new Intl.DateTimeFormat("ru-RU");
const DATE_TIME_FORMATTER = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatMoney(value: number | string): string {
  const n = typeof value === "string" ? Number(value) : value;
  return MONEY_FORMATTER.format(Number.isFinite(n) ? n : 0);
}

/** Крупное число одним взглядом: 4 809 747 848 → «4,81 млрд».
 *
 * На дашборде важен порядок величины, а не копейки: полное число из
 * двенадцати цифр читается дольше, чем занимает вся карточка. Точное
 * значение остаётся в подсказке — см. formatMoney.
 */
export function formatCompact(value: number | string): string {
  const n = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(n)) return "0";
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

/** Строка долга в том виде, в каком её отдаёт бэкенд. */
interface DebtRow {
  debt_total?: string | null;
  debt_currency?: string | null;
  debt_by_currency?: Record<string, string> | null;
  currency?: string | null;
}

/** Долги по валютам. Складывать KZT и USD нельзя — 1000 ₸ и 5 $ не дают 1005.
 *
 * Бэкенд отдаёт разбивку в debt_by_currency, а плоское debt_total описывает
 * только основную валюту клиента. Правило одно на всю систему, поэтому живёт
 * здесь, а не копией на каждой странице.
 */
export function sumDebtByCurrency(rows: readonly DebtRow[]): Record<string, number> {
  return rows.reduce<Record<string, number>>((totals, row) => {
    const parts = row.debt_by_currency ?? {
      [row.debt_currency ?? row.currency ?? "KZT"]: row.debt_total ?? "0",
    };
    for (const [currency, amount] of Object.entries(parts)) {
      const parsed = Number(amount || 0);
      totals[currency] = (totals[currency] ?? 0) + (Number.isFinite(parsed) ? parsed : 0);
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
    const amount = Number(amountOf(row) ?? 0);
    totals[currency] = (totals[currency] ?? 0) + (Number.isFinite(amount) ? amount : 0);
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

export function monthStartLocalIsoDate(): string {
  const today = new Date();
  return toLocalIsoDate(new Date(today.getFullYear(), today.getMonth(), 1));
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

/** Дата и время по-русски: «13.07.2026, 14:32». */
export function formatDateTime(value: string | Date): string {
  return DATE_TIME_FORMATTER.format(new Date(value));
}

// Телефон по шаблону +7 (___) ___-__-__ — форматирует ввод по мере набора.
export function formatPhone(value: string): string {
  const digits = value.replace(/\D/g, "").replace(/^8/, "7").slice(0, 11);
  if (!digits) return "";
  const d = digits.startsWith("7") ? digits : "7" + digits;
  const p = d.slice(0, 11);
  const a = p.slice(1, 4);
  const b = p.slice(4, 7);
  const c = p.slice(7, 9);
  const e = p.slice(9, 11);
  let out = "+7";
  if (a) out += ` (${a}`;
  if (a.length === 3) out += ")";
  if (b) out += ` ${b}`;
  if (c) out += `-${c}`;
  if (e) out += `-${e}`;
  return out;
}
