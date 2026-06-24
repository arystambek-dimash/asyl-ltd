import { api } from "@/lib/api";
import type { Order } from "@/lib/types";

export interface PaymentInfo {
  kaspi_qr: string; bank: string; account: string; instructions: string;
}
export interface RegisterPayload {
  username: string; password: string; first_name: string;
  last_name: string; phone: string; iin?: string;
}

export const payOrder = (id: number, method: "card" | "kaspi") =>
  api.post<Order>(`/portal/orders/${id}/pay/`, { method }).then((r) => r.data);

export const requestDebt = (id: number) =>
  api.post<Order>(`/portal/orders/${id}/request-debt/`).then((r) => r.data);

export const setTruck = (id: number, truck_number: string) =>
  api.patch<Order>(`/portal/orders/${id}/truck/`, { truck_number }).then((r) => r.data);

export const getPaymentInfo = () =>
  api.get<PaymentInfo>("/portal/payment-info/").then((r) => r.data);

export const registerClient = (payload: RegisterPayload) =>
  api.post<{ access: string; refresh: string }>("/portal/register/", payload).then((r) => r.data);

export type ClientStep = "pending" | "pay" | "rejected" | "truck" | "shipping" | "done";

export function clientStep(status: string): ClientStep {
  if (status === "pending" || status === "draft") return "pending";
  if (status === "rejected" || status === "cancelled") return "rejected";
  // Новый порядок: подтверждён → ввод КАМАЗа, въезд → оплата, дальше склад.
  if (status === "confirmed") return "truck";
  if (status === "arrived") return "pay";
  if (status === "shipped") return "done";
  return "shipping"; // paid/loading/loaded
}
