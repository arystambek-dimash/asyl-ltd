import type { FixationDraft } from "@/components/orders/fixation-fields";
import type { Order } from "@/lib/types";

/**
 * Черновик формы «Новый заказ»: переживает случайное закрытие окна и перезагрузку.
 * Живёт в localStorage этого браузера, отдельно на каждого сотрудника.
 */
export interface OrderDraft {
  /** Шаблон, от которого начали заказ; черновик восстанавливается только под тем же шаблоном. */
  template: Order | null;
  dept: string;
  client: string;
  currency: "KZT" | "USD";
  store: string;
  warehouse: string;
  transport: "truck" | "train";
  truck: string;
  wagonNumber: string;
  arrival: string;
  rows: { id: number; product: string; quantity: string; price: string }[];
  backdateOn: boolean;
  fixation: FixationDraft;
}

const KEY = "asyl_order_draft_v1";

function storageKey(userId: number | undefined) {
  return userId ? `${KEY}:${userId}` : null;
}

export function loadOrderDraft(userId: number | undefined): OrderDraft | null {
  const key = storageKey(userId);
  if (!key) return null;
  try {
    const raw = localStorage.getItem(key);
    const draft = raw ? (JSON.parse(raw) as OrderDraft) : null;
    return draft && Array.isArray(draft.rows) && draft.fixation ? draft : null;
  } catch {
    return null;
  }
}

export function saveOrderDraft(userId: number | undefined, draft: OrderDraft) {
  const key = storageKey(userId);
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(draft));
  } catch {
    // Хранилище недоступно (приватный режим) — форма просто работает без черновика.
  }
}

export function clearOrderDraft(userId: number | undefined) {
  const key = storageKey(userId);
  if (!key) return;
  try {
    localStorage.removeItem(key);
  } catch {
    // см. saveOrderDraft
  }
}

/** Есть ли в черновике что-то, введённое руками (склад и отдел подставляются сами). */
export function orderDraftHasContent(draft: OrderDraft) {
  return Boolean(
    draft.template ||
    draft.client ||
    draft.truck ||
    draft.wagonNumber ||
    draft.arrival ||
    draft.backdateOn ||
    draft.rows.some((row) => row.product || row.quantity || row.price),
  );
}
