"use client";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";
import { useConfirmAction, type ConfirmAction } from "@/lib/use-confirm-action";

/** Окончательное удаление заказа из архива: окно открывают из архива страницы и из панели архива. */
export function useOrderPurge(onPurged: () => void): ConfirmAction<Order> {
  return useConfirmAction<Order>(async (order) => {
    await api.delete(`/orders/${order.id}/purge/`);
    onPurged();
  });
}

export function OrderPurgeDialog({ action }: { action: ConfirmAction<Order> }) {
  const order = action.item;
  return (
    <ConfirmDialog
      {...action.dialog}
      title="Удалить заказ из архива?"
      description={
        order
          ? `Заказ #${order.id} (${order.client_name ?? "клиент"}) исчезнет из архива, восстановить его будет нельзя. Проведённые оплаты, отгрузка и история AI останутся в учёте.`
          : ""
      }
      confirmLabel="Удалить из архива"
    />
  );
}
