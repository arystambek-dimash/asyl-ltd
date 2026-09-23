"use client";
import { OrderConfirmation } from "@/components/order-confirmation";
import { OrderRejectionDialog } from "@/components/order-rejection-dialog";
import { ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import type { Department, Order } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import type { OrderRequests } from "./use-order-requests";

/** Подтверждение и отклонение заявки — одни и те же окна на десктопе и телефоне. */
export function OrderReviewDialogs({
  requests,
  confirming,
  rejecting,
  onConfirmClose,
  onRejectClose,
}: {
  requests: OrderRequests;
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
            void requests.reload();
          }}
        />
      )}
      <Modal
        open={!!confirming}
        onClose={() => {
          if (!requests.busy) onConfirmClose();
        }}
        eyebrow="Подтверждение"
        title={`Заказ #${confirming?.id ?? ""}`}
        mobileFullscreen
      >
        {departmentsError ? (
          <ErrorAlert message={departmentsError} onRetry={retryDepartments} />
        ) : (
          confirming && (
            <OrderConfirmation
              key={confirming.id}
              order={confirming}
              departments={departments ?? []}
              busy={requests.busy}
              error={requests.actionError}
              onConfirm={async (payload) => {
                if (await requests.confirm(confirming, payload)) onConfirmClose();
              }}
            />
          )
        )}
      </Modal>
    </>
  );
}
