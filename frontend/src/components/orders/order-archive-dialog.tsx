"use client";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api } from "@/lib/api";
import type { Order } from "@/lib/types";
import { useConfirmAction, type ConfirmAction } from "@/lib/use-confirm-action";

/** Мягкое удаление заказа в архив: из списка заказов и из карточки — одно действие и одно окно. */
export function useOrderArchive(onArchived: () => void): ConfirmAction<Order> {
  return useConfirmAction<Order>(async (order) => {
    await api.delete(`/orders/${order.id}/`);
    onArchived();
  });
}

export function OrderArchiveDialog({ action }: { action: ConfirmAction<Order> }) {
  const order = action.item;
  return (
    <ConfirmDialog
      {...action.dialog}
      title="Переместить заказ в архив?"
      description={
        order
          ? `Заказ #${order.id} (${order.client_name ?? "клиент"}) исчезнет из рабочих списков и отчётов. Его можно будет восстановить из архива заказов.`
          : ""
      }
      confirmLabel="В архив"
    />
  );
}
