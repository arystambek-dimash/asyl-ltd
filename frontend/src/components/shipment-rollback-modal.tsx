"use client";

import { useEffect, useState } from "react";
import { LoaderCircle, RotateCcw, ShieldAlert, VideoOff } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { ORDER_STATUS_LABELS } from "@/lib/constants";
import type { Order } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";

export function ShipmentRollbackModal({
  order,
  initialTarget = "confirmed",
  onClose,
  onChanged,
}: {
  order: Order | null;
  initialTarget?: "pending" | "confirmed" | "cancelled";
  onClose: () => void;
  onChanged: (order: Order) => void | Promise<void>;
}) {
  const [target, setTarget] = useState(initialTarget);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!order) return;
    setTarget(initialTarget);
    setReason("");
    setError("");
  }, [initialTarget, order]);

  if (!order) return null;

  async function submit() {
    setBusy(true);
    setError("");
    try {
      const response = await api.post<{ order: Order }>(`/orders/${order!.id}/rollback-shipment/`, {
        status: target,
        reason: reason.trim(),
      });
      await onChanged(response.data.order);
      onClose();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      eyebrow={`Контролируемый откат · заказ #${order.id}`}
      title="Отменить отгрузку?"
      description="Это действие вернёт товар на склад и освободит заказ для повторной обработки."
      className="max-w-xl"
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Закрыть
          </Button>
          <Button variant="destructive" disabled={busy || reason.trim().length < 5} onClick={() => void submit()}>
            {busy ? <LoaderCircle className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}
            Откатить отгрузку
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 sm:grid-cols-2">
          <div className="flex gap-2">
            <ShieldAlert className="mt-0.5 size-4 shrink-0" />
            <span>Автор и причина навсегда сохранятся в журнале.</span>
          </div>
          <div className="flex gap-2">
            <VideoOff className="mt-0.5 size-4 shrink-0" />
            <span>Видео удалится сразу; если ПК камер недоступен — по локальному сроку хранения.</span>
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="rollback-status">Новый статус</Label>
          <Select
            id="rollback-status"
            value={target}
            onChange={(event) => setTarget(event.target.value as typeof target)}
          >
            {(["confirmed", "pending", "cancelled"] as const).map((status) => (
              <option key={status} value={status}>
                {ORDER_STATUS_LABELS[status]}
              </option>
            ))}
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="rollback-reason">Причина отката</Label>
          <textarea
            id="rollback-reason"
            autoFocus
            maxLength={500}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Например: ошибочно завершили не тот заказ"
            className="min-h-24 w-full resize-y rounded-xl border bg-[var(--background)] px-3 py-2 text-sm outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15"
          />
          <span className="text-xs text-[var(--muted-foreground)]">Обязательно, минимум 5 символов.</span>
        </div>
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
