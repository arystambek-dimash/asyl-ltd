"use client";
import { useCallback, useRef, useState } from "react";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { Order, PaymentQueueItem } from "@/lib/types";
import { usePagedApi } from "@/lib/use-paged-api";
import { apiUrl, filtersAreValid, scopeParams, type CashFilters } from "./filters";

/* ── «Оплаты» кассы: данные и действия, общие для вкладок ─────────────── */
// Хук отдаёт оплаты к подтверждению и заказы, которые ждут оплаты, а об
// изменениях сообщает наружу — сводки главной перезагружаются сами.
// Оплаты к подтверждению — общая очередь всех отделов; «Ждут оплаты» — отдел кассы.
export function useCashierQueue(
  enabled: boolean,
  queueFilters: CashFilters,
  awaitingFilters: CashFilters,
  onChanged?: () => Promise<unknown>,
) {
  const queueActive = enabled && filtersAreValid(queueFilters);
  const awaitingActive = enabled && filtersAreValid(awaitingFilters);
  const queuePage = usePagedApi<PaymentQueueItem>(
    queueActive ? apiUrl("/orders/payments-queue/", scopeParams(queueFilters)) : null,
  );
  const awaitingPage = usePagedApi<Order>(
    awaitingActive ? apiUrl("/orders/awaiting-payment/", scopeParams(awaitingFilters)) : null,
  );
  const { reload: reloadQueue } = queuePage;
  const { reload: reloadAwaiting } = awaitingPage;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const loadError = queuePage.error || awaitingPage.error;

  const refresh = useCallback(async () => {
    await Promise.all([reloadQueue(), reloadAwaiting()]);
  }, [reloadAwaiting, reloadQueue]);
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
    loading: queuePage.loading || awaitingPage.loading,
    refresh,
    toReview: queuePage.items,
    awaiting: awaitingPage.items,
    queuePage,
    awaitingPage,
    busy,
    error,
    loadError,
    reload: reloadAll,
    confirmPayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/confirm/`), "Оплата подтверждена"),
    receivePayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/receive/`), "Поступление подтверждено"),
    rejectPayment: (p: PaymentQueueItem) =>
      act(() => api.post(`/orders/${p.order}/payments/${p.id}/reject/`), "Оплата отклонена"),
    moveToDebt: (order: Order) => act(() => api.post(`/orders/${order.id}/to-debt/`), "Долг согласован"),
  };
}

export type CashierQueue = ReturnType<typeof useCashierQueue>;
