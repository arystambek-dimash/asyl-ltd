"use client";
import { useCallback, useRef, useState } from "react";
import type { OrderConfirmationData } from "@/components/order-confirmation";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { Order } from "@/lib/types";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";

/**
 * Заявки на заказ: `confirm_queue=1` добавляет заявки клиентов без отдела (отдел забирает клиента к себе),
 * а сотруднику с правом «Заявки всех отделов» открывает заявки всех отделов.
 */
export const ORDER_REQUESTS_URL = "/orders/?status_group=pending&confirm_queue=1";

/** Заявки для вкладки «Заявки» в «Заказах»: список, подтверждение и опрос раз в 30 секунд. */
export function useOrderRequests(enabled: boolean, onChanged?: () => unknown) {
  const page = usePagedApi<Order>(enabled ? ORDER_REQUESTS_URL : null);
  const { reload: reloadPage } = page;
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  const inFlight = useRef(false);

  const reload = useCallback(async () => {
    await Promise.all([reloadPage(), onChanged?.()]);
  }, [onChanged, reloadPage]);

  async function confirm(order: Order, payload: OrderConfirmationData) {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setActionError("");
    try {
      await api.post(`/orders/${order.id}/confirm/`, payload);
      await reload();
      showSuccess("Заказ подтверждён");
      return true;
    } catch (cause) {
      setActionError(apiError(cause));
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  useVisiblePolling(reloadPage, 30_000, enabled && !busy && !page.loadingMore && page.items.length <= 50);

  return { ...page, busy, actionError, clearActionError: () => setActionError(""), confirm, reload };
}

export type OrderRequests = ReturnType<typeof useOrderRequests>;
