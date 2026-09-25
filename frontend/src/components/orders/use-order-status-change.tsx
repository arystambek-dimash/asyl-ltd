"use client";
import { useState } from "react";
import { ManualOrderStatusModal, type ManualOrderTarget } from "@/components/manual-order-status-modal";
import { ShipmentRollbackModal } from "@/components/shipment-rollback-modal";
import { api, apiError } from "@/lib/api";
import { ORDER_REVIEWABLE_STATUSES } from "@/lib/constants";
import type { Order } from "@/lib/types";

type RollbackTarget = "pending" | "confirmed" | "cancelled";

/**
 * Ручная смена статуса заказа — одна маршрутизация для списка и карточки:
 * заявка → окно подтверждения, отгруженный → контролируемый откат,
 * «Отгружено»/«Отменён» → окно с числом мешков или отменой, остальное — сразу.
 * Без orders.edit смена уходит запросом на одобрение: мешков такой запрос не несёт.
 */
export function useOrderStatusChange({
  canEdit,
  canRollback,
  onConfirm,
  onChanged,
}: {
  canEdit: boolean;
  canRollback: boolean;
  onConfirm: (order: Order) => void;
  onChanged: () => unknown;
}) {
  const [manual, setManual] = useState<{ order: Order; target: ManualOrderTarget } | null>(null);
  const [rollback, setRollback] = useState<{ order: Order; target: RollbackTarget } | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");

  async function apply(order: Order, target: string) {
    setBusyId(order.id);
    setError("");
    try {
      const response = await api.post<{ applied: boolean }>(`/orders/${order.id}/set-status/`, { status: target });
      if (response.data?.applied === false) setError("Запрос на смену статуса отправлен на одобрение.");
      await onChanged();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusyId(null);
    }
  }

  function choose(order: Order, target: string) {
    setError("");
    if (ORDER_REVIEWABLE_STATUSES.includes(order.status) && target === "confirmed") {
      onConfirm(order);
      return;
    }
    if (order.status === "shipped" && target !== "shipped") {
      setRollback({ order, target: target as RollbackTarget });
      return;
    }
    if (canEdit && (target === "shipped" || target === "cancelled")) {
      setManual({ order, target });
      return;
    }
    void apply(order, target);
  }

  const dialogs = (
    <>
      <ManualOrderStatusModal
        order={manual?.order ?? null}
        target={manual?.target ?? null}
        onClose={() => setManual(null)}
        onChanged={onChanged}
      />
      <ShipmentRollbackModal
        order={rollback?.order ?? null}
        initialTarget={rollback?.target}
        onClose={() => setRollback(null)}
        onChanged={onChanged}
      />
    </>
  );

  return {
    choose,
    /** Отгруженный заказ меняет статус только откатом — без orders.rollback выбора нет. */
    canChoose: (order: Order) => order.status !== "shipped" || canRollback,
    busyId,
    error,
    dialogs,
  };
}
