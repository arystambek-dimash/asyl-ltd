import type { FixationDraft } from "@/components/orders/fixation-fields";
import type { PayNowDraft } from "@/components/orders/pay-now";
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
  /** Прицеп (тягач + прицеп); в черновиках до прицепа поля нет. */
  trailer?: string;
  wagonNumber: string;
  arrival: string;
  rows: { id: number; product: string; quantity: string; price: string }[];
  backdateOn: boolean;
  fixation: FixationDraft;
  /**
   * «Оплата сразу»; в черновиках до неё поля нет. Восстанавливается выключенной
   * (см. loadOrderDraft), способ и сумма остаются.
   */
  payNow?: PayNowDraft;
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
    if (!draft || !Array.isArray(draft.rows) || !draft.fixation) return null;
    // «Оплата сразу» возвращается выключенной: деньги не должны записаться по
    // черновику, который не перепроверили. Способ и сумма остаются подсказкой.
    return draft.payNow ? { ...draft, payNow: { ...draft.payNow, on: false } } : draft;
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

/**
 * Есть ли в черновике что-то, введённое руками (склад и отдел подставляются сами).
 * «Оплата сразу» не в счёт: восстанавливается она всё равно выключенной.
 */
export function orderDraftHasContent(draft: OrderDraft) {
  return Boolean(
    draft.template ||
    draft.client ||
    draft.truck ||
    draft.trailer ||
    draft.wagonNumber ||
    draft.arrival ||
    draft.backdateOn ||
    draft.rows.some((row) => row.product || row.quantity || row.price),
  );
}
