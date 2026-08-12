"use client";

import { useEffect, useState } from "react";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api, apiError } from "@/lib/api";
import type { Order } from "@/lib/types";

export function OrderPurgeDialog({
  order,
  onClose,
  onPurged,
}: {
  order: Order | null;
  onClose: () => void;
  onPurged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const orderId = order?.id ?? null;

  useEffect(() => {
    if (orderId === null) return;
    setError("");
  }, [orderId]);

  async function purge() {
    if (!order || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.delete(`/orders/${order.id}/purge/`);
      onClose();
      onPurged();
    } catch (cause) {
      // Keep the confirmation open on a real API failure so the user sees
      // why the order is still present in the archive.
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog
      open={order !== null}
      onClose={() => {
        if (!busy) onClose();
      }}
      title="Удалить заказ из архива?"
      description={
        order
          ? `Заказ #${order.id} (${order.client_name ?? "клиент"}) исчезнет из архива, восстановить его будет нельзя. Проведённые оплаты, отгрузка и история AI останутся в учёте.`
          : ""
      }
      confirmLabel="Удалить из архива"
      busy={busy}
      error={error}
      onConfirm={() => void purge()}
    />
  );
}
