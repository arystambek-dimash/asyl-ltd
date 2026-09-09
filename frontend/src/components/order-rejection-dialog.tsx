"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { showSuccess } from "@/lib/toast";
import type { Order } from "@/lib/types";

export function OrderRejectionDialog({
  order,
  onClose,
  onDone,
}: {
  order: Order;
  onClose: () => void;
  onDone: () => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  return (
    <Modal
      open
      onClose={() => {
        if (!inFlight.current) onClose();
      }}
      title={`Отклонить заявку #${order.id}`}
    >
      <form
        className="grid gap-4"
        onSubmit={async (event) => {
          event.preventDefault();
          if (inFlight.current || !reason.trim()) return;
          inFlight.current = true;
          setBusy(true);
          setError("");
          try {
            await api.post(`/orders/${order.id}/reject/`, { reason: reason.trim() });
          } catch (e) {
            setError(apiError(e));
            return;
          } finally {
            inFlight.current = false;
            setBusy(false);
          }
          showSuccess("Заявка отклонена");
          onDone();
        }}
      >
        <p className="text-sm text-[var(--muted-foreground)]">
          {order.client_name}. Причину увидит клиент в своём заказе.
        </p>
        <label className="grid gap-2 text-sm font-medium">
          Причина отклонения
          <textarea
            required
            maxLength={500}
            rows={3}
            value={reason}
            disabled={busy}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Например: нужного товара сейчас нет в наличии"
            className="w-full rounded-lg border bg-[var(--background)] p-3 font-normal focus:outline-none focus:ring-2 focus:ring-[var(--ring)]"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            Назад
          </Button>
          <Button type="submit" variant="destructive" disabled={busy || !reason.trim()}>
            {busy ? "Отклонение…" : "Отклонить заявку"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
