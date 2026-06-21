import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatMoney(value: number | string): string {
  const n = typeof value === "string" ? Number(value) : value;
  return new Intl.NumberFormat("ru-RU", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(n);
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
