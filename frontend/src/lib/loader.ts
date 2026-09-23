import { api } from "@/lib/api";
import type { TransportPair } from "@/lib/plates";
import type { ShipmentWagon } from "@/lib/types";
import { downloadBlob } from "@/lib/download";
import { readStoredChoice, storeChoice, userChoiceKey } from "@/lib/stored-choice";
import { formatMoney, toLocalIsoDate } from "@/lib/utils";

export interface LoaderOrderItem {
  label: string;
  quantity: number;
  weight_kg: string;
  unit_price: string | null;
}

/** Заказ на экране грузчика (GET /loader/queue/ и /loader/history/). */
export interface LoaderOrder {
  id: number;
  status: string;
  transport_type: "truck" | "train";
  truck_number: string;
  trailer_number?: string;
  /** Прошлые пары клиента — чипы «как в прошлый раз». */
  transport_suggestions?: TransportPair[];
  /** Пару указал клиент: грузчик её не меняет. */
  transport_locked?: boolean;
  currency: "KZT" | "USD";
  arrival_date: string | null;
  created_at: string;
  client_name: string;
  /** Страна клиента — страна номера по умолчанию. */
  client_country?: string;
  items: LoaderOrderItem[];
  bags: number;
  total_kg: string;
  total_amount: string;
  shipped_at: string | null;
  /** Оплата заказа: грузчик видит, платил ли клиент заранее. */
  payment_status?: string;
  paid_total?: string;
  remaining_amount?: string;
  /** Грузчик может сам отменить эту отгрузку (своя и не старше часа). */
  can_rollback?: boolean;
  /** Отгрузка по отчёту о вагонах: станция, вагоны и отчёт в формате владельца. */
  rail_station?: string;
  wagons?: ShipmentWagon[];
  rail_report_text?: string;
}

export interface WaybillSigner {
  role: string;
  name: string;
}

export interface WaybillSettings {
  point_name: string;
  signers: WaybillSigner[];
}

/** Вес груза: у фуры — килограммы, у вагона — тонны (1360 мешков по 50 кг = 68 т). */
export function loadWeight(order: Pick<LoaderOrder, "transport_type" | "total_kg">): string {
  const kg = Number(order.total_kg);
  return order.transport_type === "train" ? `${formatMoney(kg / 1000)} т` : `${kg} кг`;
}

/** Последняя вкладка «Фуры | Вагоны» — своя у каждого на общем планшете. */
export function readStoredLoaderTransport(userId: number): string | null {
  return readStoredChoice(userChoiceKey("loader:transport", userId));
}

export function storeLoaderTransport(transport: LoaderOrder["transport_type"], userId: number) {
  storeChoice(userChoiceKey("loader:transport", userId), transport);
}

export function shiftIsoDate(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  return toLocalIsoDate(new Date(year, month - 1, day + days));
}

export function loaderUrl(path: "queue" | "history", params: Record<string, string>) {
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value));
  const suffix = query.toString();
  return `/loader/${path}/${suffix ? `?${suffix}` : ""}`;
}

/**
 * Открыть накладную для печати. Вкладку открываем сразу по нажатию — после
 * ожидания ответа браузер счёл бы её всплывающим окном и заблокировал.
 */
export async function openWaybill(orderId: number) {
  const tab = window.open("", "_blank");
  try {
    const { data } = await api.get<Blob>(`/loader/orders/${orderId}/waybill/`, { responseType: "blob" });
    if (!tab) {
      downloadBlob(data, `nakladnaya_${orderId}.pdf`);
      return;
    }
    const url = URL.createObjectURL(data);
    tab.location.href = url;
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (error) {
    tab?.close();
    throw error;
  }
}
