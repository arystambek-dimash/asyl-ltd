import { api } from "@/lib/api";
import { downloadBlob } from "@/lib/download";
import type { PortalOrder, PortalPaymentMethod } from "@/lib/types";

export interface PaymentInfo {
  kaspi_qr: string;
  bank: string;
  account: string;
  instructions: string;
}
export interface RegisterPayload {
  username: string;
  password: string;
  first_name: string;
  last_name: string;
  company_name: string;
  phone: string;
  iin: string;
}

export const payOrder = (
  id: number,
  method: PortalPaymentMethod,
  options?: { phone_number?: string; amount?: string },
) =>
  api
    .post<PortalOrder & { payment_redirect_url?: string }>(`/portal/orders/${id}/pay/`, { method, ...options })
    .then((r) => r.data);

export const releasePortalPayment = (orderId: number, paymentId: number) =>
  api.post<PortalOrder>(`/portal/orders/${orderId}/payments/${paymentId}/release/`).then((r) => r.data);

export const setTruck = (id: number, truck_number: string) =>
  api.patch<PortalOrder>(`/portal/orders/${id}/truck/`, { truck_number }).then((r) => r.data);

export const getPaymentInfo = () => api.get<PaymentInfo>("/portal/payment-info/").then((r) => r.data);

export async function downloadInvoice(id: number) {
  const response = await api.get<Blob>(`/portal/orders/${id}/invoice/`, {
    responseType: "blob",
  });
  downloadBlob(response.data, `schet_na_oplatu_${id}.pdf`);
}

export async function downloadReceipt(id: number) {
  const response = await api.get<Blob>(`/portal/orders/${id}/receipt/`, {
    responseType: "blob",
  });
  downloadBlob(response.data, `receipt_order_${id}.pdf`);
}

export const registerClient = (payload: RegisterPayload) =>
  api.post<{ access: string; refresh: string }>("/portal/register/", payload).then((r) => r.data);

export type ClientStep = "pending" | "pay" | "rejected" | "truck" | "shipping" | "done";

export function clientStep(status: string, paymentStatus?: string, hasPendingPayment = false): ClientStep {
  if (status === "pending" || status === "draft") return "pending";
  if (status === "rejected" || status === "cancelled") return "rejected";
  // Подтверждён → ввод КАМАЗа → склад → отгрузка → оплата.
  if (status === "confirmed") return "truck";
  // A late QR can settle the order while a replacement invoice is still
  // externally payable. Keep the payment controls visible until that extra
  // reservation is safely cancelled/closed.
  if (status === "shipped") return paymentStatus === "settled" && !hasPendingPayment ? "done" : "pay";
  return "shipping";
}
