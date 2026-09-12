"use client";
import { useCallback, useRef, useState } from "react";
import type { OrderConfirmationData } from "@/components/order-confirmation";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { CashierLogItem, Order, PaymentQueueItem } from "@/lib/types";
import { usePagedApi } from "@/lib/use-paged-api";
import { apiUrl, filtersAreValid, scopeParams, type CashFilters } from "./filters";

/* ── Очередь кассира: данные и действия, общие для вкладок ─────────────── */
// Журнал живёт на своей вкладке со своими фильтрами и ленивой подгрузкой —
// хук очереди отдаёт только заявки и оплаты, а об изменениях сообщает
// наружу, чтобы журнал перезагрузил себя сам.
export function useCashierQueue(
  enabled: boolean,
  canReviewOrders: boolean,
  queueFilters: CashFilters,
  onChanged?: () => Promise<unknown>,
) {
  const queueActive = enabled && filtersAreValid(queueFilters);
  const queueParams = scopeParams(queueFilters);
  // Кассе нужны заявки на подтверждение и оплаты — отбор отдела общий.
  const pendingPage = usePagedApi<Order>(
    queueActive && canReviewOrders ? apiUrl("/orders/", { ...queueParams, status_group: "pending" }) : null,
  );
  const queuePage = usePagedApi<PaymentQueueItem>(queueActive ? apiUrl("/orders/payments-queue/", queueParams) : null);
  const { reload: reloadPending } = pendingPage;
  const { reload: reloadQueue } = queuePage;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const loadError = pendingPage.error || queuePage.error;

  const refresh = useCallback(async () => {
    await Promise.all([reloadPending(), reloadQueue()]);
  }, [reloadPending, reloadQueue]);
  async function reloadAll() {
    await Promise.all([refresh(), onChanged?.()]);
  }

  const mutationInFlight = useRef(false);
  async function act(fn: () => Promise<unknown>, done?: string) {
    if (mutationInFlight.current) return false;
    mutationInFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
      await reloadAll();
      // Без подтверждения удачное действие выглядит как «ничего не произошло»,
      // и кассир жмёт кнопку второй раз.
      if (done) showSuccess(done);
      return true;
    } catch (e) {
      setError(apiError(e));
      return false;
    } finally {
      mutationInFlight.current = false;
      setBusy(false);
    }
  }

  return {
    loading: pendingPage.loading || queuePage.loading,
    refresh,
    pendingOrders: pendingPage.items,
    toReview: queuePage.items,
    pendingPage,
    queuePage,
    busy,
    error,
    loadError,
    reload: reloadAll,
    confirmOrder: (o: Order, payload: OrderConfirmationData) =>
      act(() => api.post(`/orders/${o.id}/confirm/`, payload), "Заказ подтверждён"),
    confirmPayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/confirm/`), "Оплата подтверждена"),
    receivePayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/receive/`), "Поступление подтверждено"),
    rejectPayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/reject/`), "Оплата отклонена"),
    reopenPayment: (event: CashierLogItem) => {
      const paymentId = event.payload.payment_id;
      if (!paymentId) return;
      act(() => api.post(`/orders/${event.order}/payments/${paymentId}/reopen/`));
    },
    restorePayment: (event: CashierLogItem) => {
      const paymentId = event.payload.payment_id;
      if (!paymentId) return;
      return act(() => api.post(`/orders/${event.order}/payments/${paymentId}/restore/`));
    },
  };
}

export type CashierQueue = ReturnType<typeof useCashierQueue>;
export type PagedCashierLog = ReturnType<typeof usePagedApi<CashierLogItem>>;
