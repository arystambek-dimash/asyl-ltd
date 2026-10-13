import { api } from "@/lib/api";

/** Предпросмотр акта сверки 1С (`POST /clients/{id}/act-import/`, preview=true): что перенесётся. */
export interface ActPreview {
  act: {
    number: string;
    period_start: string;
    period_end: string;
    counterparty: string;
    currency: string;
    /** Сальдо со знаком: плюс — клиент должен нам, минус — мы клиенту. */
    opening: string;
    closing: string;
    rows: number;
  };
  /** Контрагент акта похож на клиента карточки. */
  name_matches: boolean;
  start: string;
  default_start: string;
  sales: { count: number; total: string };
  payments: { count: number; total: string };
  /** Документы, уже перенесённые раньше (этим или другим актом). */
  imported: number;
  /** Документы до даты «Импортировать с»: их уже ведёт CRM. */
  before_start: number;
  /** Сальдо на начало в долг клиента, которое станет заказом; `null` — нет. */
  opening_order: string | null;
  /** Свои записи CRM в валюте акта за его период. */
  crm: { orders: number; orders_total: string; payments: number; payments_total: string; last_day: string | null };
  /** Сальдо клиента в CRM после импорта, со знаком как у акта. */
  balance_after: string;
  /** Почему импорт невозможен; пусто — можно. */
  problems: string[];
}

/** Ответ одной порции переноса: окно повторяет запрос, пока не придёт `finished`. */
export interface ActChunk {
  processed: number;
  payments: number;
  remaining: number;
  finished: boolean;
  /** Переплата, записанная в конце («мы должны клиенту»); до конца — `null`. */
  overpaid: string | null;
}

function actForm(file: File, preview: boolean, start?: string) {
  const form = new FormData();
  form.append("file", file);
  form.append("preview", String(preview));
  if (start) form.append("start", start);
  return form;
}

export async function previewClientAct(clientId: number, file: File, start?: string, signal?: AbortSignal) {
  const url = `/clients/${clientId}/act-import/`;
  return (await api.post<ActPreview>(url, actForm(file, true, start), { signal })).data;
}

export async function importClientActChunk(clientId: number, file: File, start: string) {
  return (await api.post<ActChunk>(`/clients/${clientId}/act-import/`, actForm(file, false, start))).data;
}
