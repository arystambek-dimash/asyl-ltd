"use client";

import { useEffect, useState } from "react";
import { CalendarClock, LoaderCircle } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { ORDER_AWAITING_SHIPMENT_STATUSES } from "@/lib/constants";
import { useAuth } from "@/store/auth";
import type { Order } from "@/lib/types";
import { toLocalIsoDate } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import {
  FixationFields,
  emptyFixationDraft,
  fixationBody,
  fixationDraftError,
  type FixationDraft,
} from "@/components/orders/fixation-fields";

/**
 * Какие заказы можно зафиксировать задним числом из списка/карточки. Суперюзер
 * открывает и оплаченный отгруженный заказ — перенести его отгрузку на другой
 * день, если отгрузка записана (у старых заказов её даты нет).
 */
export function canFixateOrder(order: Order, { superuser = false } = {}): boolean {
  if (ORDER_AWAITING_SHIPMENT_STATUSES.includes(order.status)) return true;
  if (order.status !== "shipped") return false;
  return !order.is_fully_paid || (superuser && Boolean(order.shipped_at));
}

export function OrderFixationModal({
  order,
  onClose,
  onChanged,
}: {
  order: Order | null;
  onClose: () => void;
  onChanged: () => unknown;
}) {
  const { me } = useAuth();
  const superuser = Boolean(me?.is_superuser);
  // Оплаченный целиком заказ доплатить нечем — остаётся только перенос отгрузки.
  const canPay = can(me, "payments.create") && !order?.is_fully_paid;
  const [draft, setDraft] = useState<FixationDraft>(emptyFixationDraft);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const shippedAlready = order?.status === "shipped";
  // Перенести можно только состоявшуюся отгрузку — у старых заказов её записи нет.
  const canMoveShipment = shippedAlready && superuser && Boolean(order?.shipped_at);
  const orderId = order?.id ?? null;
  const orderStatus = order?.status ?? "";

  // Сбрасываем черновик по id/статусу, а не по объекту: карточка заказа
  // переопрашивает API в фоне, и новый объект стирал бы выбранную дату.
  useEffect(() => {
    if (orderId === null) return;
    setDraft({
      ...emptyFixationDraft(),
      status: orderStatus === "shipped" ? "" : "shipped",
      // Окно могут открыть ради переноса отгрузки — тогда оплату отмечают сами.
      paid: orderStatus === "shipped" && canPay && !canMoveShipment,
    });
    setError("");
  }, [orderId, orderStatus, canPay, canMoveShipment]);

  if (!order) return null;
  const movingShipment = canMoveShipment && draft.status === "shipped";
  const draftError =
    movingShipment && draft.date === toLocalIsoDate(new Date(order.shipped_at!))
      ? "Отгрузка уже стоит на этой дате."
      : fixationDraftError(draft, { orderStatus: order.status, currency: order.currency });
  const nothingToDo = !draft.status && !draft.paid;

  async function apply() {
    setBusy(true);
    setError("");
    try {
      await api.post(`/orders/${order!.id}/fixate/`, fixationBody(draft));
      await onChanged();
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
        canMoveShipment
          ? canPay
            ? "Заказ уже отгружен — можно перенести отгрузку на другой день и зафиксировать оплату."
            : "Заказ уже отгружен и оплачен — можно перенести отгрузку на другой день."
          : shippedAlready
            ? "Заказ уже отгружен — можно зафиксировать оплату нужной датой."
            : canPay
              ? "Проставить отгрузку или оплату (в том числе предоплату) задним числом без поста и кассы."
              : "Проставить отгрузку задним числом без поста."
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
          currency={order.currency}
          orderStatus={order.status}
          canMoveShipment={canMoveShipment}
          idPrefix={`fixation-${order.id}`}
        />
        <FormError message={error || (draft.date ? draftError : null)} className="rounded-xl py-2.5 font-medium" />
      </div>
    </Modal>
  );
}
