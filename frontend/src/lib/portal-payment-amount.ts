import { formatMoney } from "@/lib/utils";

/** Не больше 10 цифр целых тенге — как ограничение суммы на сервере. */
const MAX_DIGITS = 10;

/** «Весь остаток»: строка без хвостовых нулей, тиыны сохраняются («50000.00» → «50000», «0.01» → «0.01»). */
export function fullAmount(value: string | null | undefined): string {
  const parsed = Number(value ?? "");
  if (!value || !Number.isFinite(parsed) || parsed <= 0) return "";
  return String(Math.round(parsed * 100) / 100);
}

/** Цифра клавиатуры: целые тенге; сумма с тиынами от «Весь остаток» набирается заново. */
export function pressAmountDigit(amount: string, digit: string): string {
  const base = amount.includes(".") ? "" : amount;
  const next = `${base}${digit}`.replace(/^0+/, "");
  return next.length > MAX_DIGITS ? amount : next;
}

/** «Стереть»: последняя цифра; сумму с тиынами — целиком. */
export function eraseAmount(amount: string): string {
  return amount.includes(".") ? "" : amount.slice(0, -1);
}

export function paymentAmountError(value: string, available: number): string {
  if (!value.trim()) return "Введите сумму оплаты.";
  const parsed = Number(value.trim().replace(",", "."));
  if (!Number.isFinite(parsed) || parsed <= 0) return "Сумма должна быть больше нуля.";
  if (Math.abs(parsed * 100 - Math.round(parsed * 100)) > 1e-7) {
    return "Укажите сумму с точностью не более двух знаков.";
  }
  if (Math.round(parsed * 100) > Math.round(available * 100)) {
    return `Доступно не более ${formatMoney(available)}.`;
  }
  return "";
}
