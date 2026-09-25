"use client";
import { OrderConfirmation, type OrderConfirmationData } from "@/components/order-confirmation";
import { OrderRejectionDialog } from "@/components/order-rejection-dialog";
import { DataGate } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import type { Department, Order } from "@/lib/types";
import { useApi } from "@/lib/use-api";

/**
 * Подтверждение и отклонение заявки — одни и те же окна во вкладке «Заявки»
 * и в карточке заказа. onConfirm возвращает true, когда заказ подтверждён.
 * Справочник отделов окно грузит само, если страница не передала свой.
 */
export function OrderReviewDialogs({
  departments: pageDepartments,
  confirming,
  rejecting,
  busy,
  error,
  onConfirm,
  onConfirmClose,
  onRejectClose,
  onRejected,
}: {
  departments?: Department[];
  confirming: Order | null;
  rejecting: Order | null;
  busy: boolean;
  error: string;
  onConfirm: (order: Order, payload: OrderConfirmationData) => Promise<boolean>;
  onConfirmClose: () => void;
  onRejectClose: () => void;
  onRejected: () => void;
}) {
  const {
    data: loadedDepartments,
    error: departmentsError,
    reload: retryDepartments,
  } = useApi<Department[]>(confirming && !pageDepartments ? "/departments/" : null);
  const departments = pageDepartments ?? loadedDepartments;
  return (
    <>
      {rejecting && (
        <OrderRejectionDialog
          key={rejecting.id}
          order={rejecting}
          onClose={onRejectClose}
          onDone={() => {
            onRejectClose();
            onRejected();
          }}
        />
      )}
      <Modal
        open={!!confirming}
        onClose={() => {
          if (!busy) onConfirmClose();
        }}
        eyebrow="Подтверждение"
        title={`Подтвердить заказ #${confirming?.id ?? ""}`}
        mobileFullscreen
      >
        {/* Форма ждёт справочник отделов: пустой список она показала бы как «Нет доступных отделов». */}
        {!departments ? (
          <DataGate loading={!departmentsError} error={departmentsError} onRetry={retryDepartments} />
        ) : (
          confirming && (
            <OrderConfirmation
              key={confirming.id}
              order={confirming}
              departments={departments}
              busy={busy}
              error={error}
              onConfirm={async (payload) => {
                if (await onConfirm(confirming, payload)) onConfirmClose();
              }}
            />
          )
        )}
      </Modal>
    </>
  );
}
