"use client";
import { useCallback, useRef, useState } from "react";
import { useQrRefundWindow } from "@/components/transactions/qr-refund-modal";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { Order, PaymentQueueItem } from "@/lib/types";
import { usePagedApi } from "@/lib/use-paged-api";
import { apiUrl, filtersAreValid, scopeParams, type CashFilters } from "./filters";

/* ── «Оплаты» кассы: данные и действия, общие для вкладок ─────────────── */
// Хук отдаёт оплаты к подтверждению и списки заказов отдела кассы, а об
// изменениях сообщает наружу — сводки главной перезагружаются сами.
// Оплаты к подтверждению — общая очередь всех отделов; заказы — отдел кассы:
// «Ждут оплаты» (отгружены, долг не согласован), «К отгрузке» (предоплата)
// и «К возврату» (переплата — только в кассе на компьютере, `refunds`).
export function useCashierQueue(
  enabled: boolean,
  queueFilters: CashFilters,
  awaitingFilters: CashFilters,
  onChanged?: () => Promise<unknown>,
  { refunds = true }: { refunds?: boolean } = {},
) {
  const queueActive = enabled && filtersAreValid(queueFilters);
  const awaitingActive = enabled && filtersAreValid(awaitingFilters);
  const ordersUrl = (path: string, active = awaitingActive) =>
    active ? apiUrl(path, scopeParams(awaitingFilters)) : null;
  const queuePage = usePagedApi<PaymentQueueItem>(
    queueActive ? apiUrl("/orders/payments-queue/", scopeParams(queueFilters)) : null,
  );
  const awaitingPage = usePagedApi<Order>(ordersUrl("/orders/awaiting-payment/"));
  const shipmentPage = usePagedApi<Order>(ordersUrl("/orders/awaiting-shipment/"));
  const refundPage = usePagedApi<Order>(ordersUrl("/orders/to-refund/", awaitingActive && refunds));
  const pages = [queuePage, awaitingPage, shipmentPage, refundPage];
  const { reload: reloadQueue } = queuePage;
  const { reload: reloadAwaiting } = awaitingPage;
  const { reload: reloadShipment } = shipmentPage;
  const { reload: reloadRefund } = refundPage;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const loadError = pages.find((page) => page.error)?.error ?? "";

  const refresh = useCallback(async () => {
    await Promise.all([reloadQueue(), reloadAwaiting(), reloadShipment(), reloadRefund()]);
  }, [reloadAwaiting, reloadQueue, reloadRefund, reloadShipment]);
  async function reloadAll() {
    await Promise.all([refresh(), onChanged?.()]);
  }
  // Возврат переплаты по Kaspi QR: строка «К возврату» уходит сразу после его
  // начала, поэтому окно ссылки держит очередь, а показывает вкладка «Оплаты».
  const qrRefund = useQrRefundWindow(reloadAll);

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
    loading: pages.some((page) => page.loading),
    /** Докручивается любая из страниц — фоновый опрос в это время не сбрасывает списки. */
    loadingMore: pages.some((page) => page.loadingMore),
    /** Самый длинный из открытых списков: опрос перечитывает только первую страницу. */
    longestList: Math.max(...pages.map((page) => page.items.length)),
    refresh,
    toReview: queuePage.items,
    awaiting: awaitingPage.items,
    toShip: shipmentPage.items,
    toRefund: refundPage.items,
    queuePage,
    awaitingPage,
    shipmentPage,
    refundPage,
    busy,
    error,
    loadError,
    reload: reloadAll,
    qrRefund,
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
