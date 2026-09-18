import { api } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import { toLocalIsoDate } from "@/lib/utils";

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
  currency: "KZT" | "USD";
  arrival_date: string | null;
  created_at: string;
  client_name: string;
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
}

export interface WaybillSigner {
  role: string;
  name: string;
}

export interface WaybillSettings {
  point_name: string;
  signers: WaybillSigner[];
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
