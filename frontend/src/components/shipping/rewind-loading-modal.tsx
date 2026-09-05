"use client";

import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import type { ShippingActionResult } from "@/components/shipping/use-shipping-actions";
import type { AiCountingSession, Order } from "@/lib/types";
import { cn } from "@/lib/utils";

/** «Вернуть в готовые к погрузке?» — сброс незавершённой погрузки. */
export function RewindLoadingModal({
  order,
  session,
  cameraName,
  onClose,
  onConfirm,
}: {
  order: Order | null;
  /** Активная сессия заказа: перед возвратом она останавливается автоматически. */
  session: AiCountingSession | null;
  /** Имя закреплённой камеры (по loading_camera), если известно. */
  cameraName?: string;
  onClose: () => void;
  /** Ошибка результата остаётся в модалке; при успехе модалка закрывается сама. */
  onConfirm: (order: Order) => Promise<ShippingActionResult>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    setError("");
    setBusy(false);
  }, [order?.id]);

  const blocked = !!session && !session.can_stop;

  async function confirm() {
    if (!order) return;
    setBusy(true);
    setError("");
    const result = await onConfirm(order);
    setBusy(false);
    if (result.ok) onClose();
    else setError(result.error);
  }

  return (
    <Modal
      open={!!order}
      onClose={() => !busy && onClose()}
      mobileFullscreen
      eyebrow={order ? `Заказ #${order.id}` : undefined}
      title="Вернуть в готовые к погрузке?"
      description="Заказ снова станет готов к погрузке, а назначенная камера освободится."
      footer={
        <>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button disabled={busy || blocked} onClick={() => void confirm()}>
            <RotateCcw className="size-4" /> {busy ? "Выполнение…" : "Вернуть в ожидание"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border p-3">
            <div className="text-[12px] text-[var(--muted-foreground)]">Сбросится</div>
            <div className="mt-1 text-[15px] font-semibold tabular-nums">{order?.bags_loaded ?? 0} меш.</div>
          </div>
          <div className="rounded-lg border p-3">
            <div className="text-[12px] text-[var(--muted-foreground)]">Камера</div>
            <div className="mt-1 truncate text-[15px] font-semibold">
              {order?.loading_camera ? cameraName || order.loading_camera : "Не назначена"}
            </div>
          </div>
        </div>
        {session && (
          <p className={cn("text-sm", blocked ? "text-[var(--destructive)]" : "text-[var(--muted-foreground)]")}>
            {blocked
              ? `AI-подсчёт запустил ${session.started_by_name || "другой сотрудник"}. Сначала он или администратор должен остановить сессию.`
              : "Перед возвратом активный AI-подсчёт будет остановлен автоматически."}
          </p>
        )}
        <p className="text-sm text-[var(--muted-foreground)]">
          Назначенная камера и текущий результат незавершённой погрузки будут очищены. Действие запишется в журнал.
        </p>
        {error && (
          <p
            role="alert"
            className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
          >
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
