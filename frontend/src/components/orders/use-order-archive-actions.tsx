"use client";
import { useState } from "react";
import { OrderPurgeDialog, useOrderPurge } from "@/components/orders/order-purge-dialog";
import { api, apiError } from "@/lib/api";
import type { Order } from "@/lib/types";

/** Восстановление и удаление заказа из архива — общие для страницы архива и стопки у кнопки. */
export function useOrderArchiveActions(onChanged: () => unknown) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const purgeAction = useOrderPurge(onChanged);

  async function restore(order: Order) {
    setBusyId(order.id);
    setError("");
    try {
      await api.post(`/orders/${order.id}/restore/`);
      void onChanged();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusyId(null);
    }
  }

  return {
    busyId,
    error,
    restore,
    purge: purgeAction.open,
    /** Открыт диалог удаления — стопка не должна схлопываться от клика по нему. */
    purging: purgeAction.item !== null,
    purgeDialog: <OrderPurgeDialog action={purgeAction} />,
  };
}
