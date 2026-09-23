/** «Вставить отчёт» у грузчика: предпросмотр отчёта о вагонах (POST /loader/rail-report/…). */

export type WagonNumberStatus = "ok" | "length" | "check_digit";

/** Почему отчёт нельзя провести без человека (или что стоит показать). */
export interface RailIssue {
  code: string;
  message: string;
  /** Строка сообщения с 1 — если причина в конкретной строке. */
  line: number | null;
  /** Номер вагона, код товара или название клиента из отчёта. */
  subject: string;
  /** Заказ, из-за которого отчёт похож на дубль. */
  order_id: number | null;
}

export interface RailPreviewWagon {
  position: number;
  line: number;
  number: string;
  number_status: WagonNumberStatus;
  code: string;
  product_id: number | null;
  product_label: string;
  tons: string;
  bags: number | null;
  unit_price: string | null;
  amount: string | null;
}

export interface RailPreviewItem {
  product_id: number;
  product_label: string;
  code: string;
  wagons: number;
  bags: number;
  unit_price: string | null;
  amount: string | null;
  reference_price: string | null;
  reference_order_id: number | null;
}

export interface RailPreview {
  /** «Отгрузить по отчёту» заранее внесённый заказ; null — новый заказ по отчёту. */
  order_id: number | null;
  day: string | null;
  country: string;
  client_name: string;
  station: string;
  declared_wagons: number | null;
  client: { id: number; name: string; profile: boolean } | null;
  currency: "KZT" | "USD" | "";
  wagons: RailPreviewWagon[];
  items: RailPreviewItem[];
  totals: { wagons: number; tons: string; bags: number; amount: string | null; currency: string };
  issues: RailIssue[];
  warnings: RailIssue[];
  unresolved: { client: string; products: string[] };
  /** Похожие ручные заказы, которые ещё ждут отгрузки: их можно отгрузить по этому отчёту. */
  shippable_orders: number[];
  ok: boolean;
  can_apply: boolean;
  can_remember_products: boolean;
  can_remember_clients: boolean;
}

/** Из чего выбирать при разрешении неизвестного (GET /loader/rail-report/options/). */
export interface RailOptions {
  products: { id: number; label: string; weight_kg: string }[];
  clients: { id: number; name: string; currency: "KZT" | "USD"; department_name: string }[];
}

export const RAIL_REPORT_API = "/loader/rail-report";

/** Тело запросов листа: текст отчёта и заказ «Отгрузить по отчёту». */
export function railReportBody(text: string, orderId: number | null) {
  return orderId === null ? { text } : { text, order: orderId };
}

export const WAGON_NUMBER_PROBLEMS: Record<Exclude<WagonNumberStatus, "ok">, string> = {
  length: "не 8 цифр",
  check_digit: "ошибка в номере",
};

/** «сб 19.09.2026»: день отчёта крупно в шапке предпросмотра. */
export function reportDayLabel(iso: string | null): string {
  if (!iso) return "без даты";
  const [year, month, day] = iso.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  const weekday = date.toLocaleDateString("ru-RU", { weekday: "short" });
  return `${weekday} ${String(day).padStart(2, "0")}.${String(month).padStart(2, "0")}.${year}`;
}

/**
 * Скопировать отчёт в буфер. На планшете без HTTPS-контекста Clipboard API
 * нет — тогда старый способ через скрытое поле. `false` — не вышло.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Браузер отказал (нет фокуса или разрешения) — пробуем старый способ.
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("readonly", "");
  field.style.position = "fixed";
  field.style.opacity = "0";
  document.body.appendChild(field);
  field.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    field.remove();
  }
}
