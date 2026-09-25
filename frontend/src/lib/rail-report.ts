/** «Вставить отчёт» у грузчика: предпросмотр отчёта о вагонах (POST /loader/rail-report/…). */

import { formatIsoDate } from "@/lib/utils";

type WagonNumberStatus = "ok" | "length" | "check_digit";

/** Почему отчёт нельзя провести без человека (или что стоит показать). */
export interface RailIssue {
  code: string;
  message: string;
  /** Строка сообщения с 1 — если причина в конкретной строке. */
  line: number | null;
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

export interface RailPreview {
  /** «Отгрузить по отчёту» заранее внесённый заказ; null — новый заказ по отчёту. */
  order_id: number | null;
  day: string | null;
  client_name: string;
  station: string;
  client: { id: number; name: string; profile: boolean } | null;
  currency: "KZT" | "USD" | "";
  wagons: RailPreviewWagon[];
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
  products: { id: number; label: string }[];
  clients: { id: number; name: string; currency: "KZT" | "USD"; department_name: string }[];
}

export const RAIL_REPORT_API = "/loader/rail-report";

/** Предел длины текста отчёта — тот же, что RAIL_REPORT_MAX_LENGTH в backend/apps/bots/parsing.py. */
export const RAIL_REPORT_MAX_LENGTH = 8192;

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
  const weekday = new Date(year, month - 1, day).toLocaleDateString("ru-RU", { weekday: "short" });
  return `${weekday} ${formatIsoDate(iso)}`;
}
