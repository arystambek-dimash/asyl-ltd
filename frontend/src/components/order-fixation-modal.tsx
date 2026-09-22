"use client";

import { useEffect, useState } from "react";
import { CalendarClock, LoaderCircle } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import type { Order } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import {
  FixationFields,
  emptyFixationDraft,
  fixationBody,
  fixationDraftError,
  type FixationDraft,
} from "@/components/orders/fixation-fields";

/** Какие заказы можно зафиксировать задним числом из списка/карточки. */
export function canFixateOrder(order: Order): boolean {
  if (["confirmed", "arrived", "loading", "loaded"].includes(order.status)) return true;
  return order.status === "shipped" && !order.is_fully_paid;
}

export function OrderFixationModal({
  order,
  onClose,
  onChanged,
}: {
  order: Order | null;
  onClose: () => void;
  onChanged: (order: Order) => void | Promise<void>;
}) {
  const { me } = useAuth();
  const canPay = can(me, "payments.create");
  const [draft, setDraft] = useState<FixationDraft>(emptyFixationDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const shippedAlready = order?.status === "shipped";
  const orderId = order?.id ?? null;
  const orderStatus = order?.status ?? "";

  // Сбрасываем черновик по id/статусу, а не по объекту: карточка заказа
  // переопрашивает API в фоне, и новый объект стирал бы выбранную дату.
  useEffect(() => {
    if (orderId === null) return;
    setDraft({
      ...emptyFixationDraft(),
      status: orderStatus === "shipped" ? "" : "shipped",
      paid: orderStatus === "shipped" && canPay,
    });
    setError("");
  }, [orderId, orderStatus, canPay]);

  if (!order) return null;
  const draftError = fixationDraftError(draft, { shippedAlready });
  const nothingToDo = !draft.status && !draft.paid;

  async function apply() {
    setBusy(true);
    setError("");
    try {
      const response = await api.post<Order>(`/orders/${order!.id}/fixate/`, fixationBody(draft));
      await onChanged(response.data);
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
      eyebrow={`Заказ #${order.id}`}
      title="Зафиксировать статус и оплату"
      className="max-w-xl"
      description={
        shippedAlready
          ? "Заказ уже отгружен — можно зафиксировать оплату нужной датой."
          : "Проставить статус и оплату задним числом без прохождения поста и кассы."
      }
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={busy || !!draftError || nothingToDo} onClick={() => void apply()}>
            {busy ? <LoaderCircle className="size-4 animate-spin" /> : <CalendarClock className="size-4" />}
            Зафиксировать
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <FixationFields
          draft={draft}
          onChange={setDraft}
          canPay={canPay}
          shippedAlready={shippedAlready}
          idPrefix={`fixation-${order.id}`}
        />
        {(error || (draftError && draft.date)) && (
          <p
            role="alert"
            className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm font-medium text-[var(--destructive)]"
          >
            {error || draftError}
          </p>
        )}
      </div>
    </Modal>
  );
}
