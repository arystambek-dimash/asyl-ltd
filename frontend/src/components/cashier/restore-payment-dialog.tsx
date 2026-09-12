"use client";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { CashierLogItem } from "@/lib/types";
import type { CashierQueue } from "./use-cashier-queue";

export function RestorePaymentDialog({
  q,
  event,
  onClose,
}: {
  q: CashierQueue;
  event: CashierLogItem | null;
  onClose: () => void;
}) {
  return (
    <ConfirmDialog
      open={!!event}
      onClose={onClose}
      title="Восстановить отклонённую оплату?"
      description={
        event
          ? `Оплата по заказу #${event.order} вернётся в очередь кассы. Само событие отмены останется в журнале.`
          : ""
      }
      confirmLabel="Восстановить"
      confirmVariant="default"
      busy={q.busy}
      error={q.error}
      onConfirm={async () => {
        if (!event) return;
        await q.restorePayment(event);
        onClose();
      }}
    />
  );
}
