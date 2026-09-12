"use client";
import { OrderConfirmation } from "@/components/order-confirmation";
import { OrderRejectionDialog } from "@/components/order-rejection-dialog";
import { ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import type { Department, Order } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { ActionError } from "./action-error";
import type { CashierQueue } from "./use-cashier-queue";

/** Подтверждение и отклонение заявки — одни и те же окна на десктопе и телефоне. */
export function OrderReviewDialogs({
  q,
  confirming,
  rejecting,
  onConfirmClose,
  onRejectClose,
}: {
  q: CashierQueue;
  confirming: Order | null;
  rejecting: Order | null;
  onConfirmClose: () => void;
  onRejectClose: () => void;
}) {
  const {
    data: departments,
    error: departmentsError,
    reload: retryDepartments,
  } = useApi<Department[]>(confirming ? "/departments/" : null);
  return (
    <>
      {rejecting && (
        <OrderRejectionDialog
          key={rejecting.id}
          order={rejecting}
          onClose={onRejectClose}
          onDone={() => {
            onRejectClose();
            void q.reload();
          }}
        />
      )}
      <Modal
        open={!!confirming}
        onClose={() => {
          if (!q.busy) onConfirmClose();
        }}
        eyebrow="Подтверждение"
        title={`Заказ #${confirming?.id ?? ""}`}
        mobileFullscreen
      >
        <ActionError message={q.error} />
        {departmentsError ? (
          <ErrorAlert message={departmentsError} onRetry={retryDepartments} />
        ) : (
          confirming && (
            <OrderConfirmation
              key={confirming.id}
              order={confirming}
              departments={departments ?? []}
              busy={q.busy}
              onConfirm={async (payload) => {
                if (await q.confirmOrder(confirming, payload)) onConfirmClose();
              }}
            />
          )
        )}
      </Modal>
    </>
  );
}
