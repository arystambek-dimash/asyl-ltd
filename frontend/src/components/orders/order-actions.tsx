"use client";
import { useState } from "react";
import dynamic from "next/dynamic";
import { Archive, CalendarClock, CircleDollarSign, Pencil } from "lucide-react";
import { OrderFixationModal, canFixateOrder } from "@/components/order-fixation-modal";
import { OrderPriceCorrectionModal } from "@/components/order-price-correction-modal";
import { OrderArchiveDialog, useOrderArchive } from "@/components/orders/order-archive-dialog";
import type { ActionMenuItem } from "@/components/ui/action-menu";
import { Modal } from "@/components/ui/modal";
import { can } from "@/lib/can";
import { ORDER_LOADING_STATUSES } from "@/lib/constants";
import type { Order } from "@/lib/types";
import { useAuth } from "@/store/auth";

const OrderForm = dynamic(() => import("@/components/order-form").then((m) => m.OrderForm));

/**
 * Действия над заказом — одно меню и одни окна для списка и карточки:
 * правка, фиксация статуса и оплаты, корректировка стоимости и архив.
 */
export function useOrderActions({ onChanged, onArchived }: { onChanged: () => unknown; onArchived: () => unknown }) {
  const { me } = useAuth();
  const canEdit = can(me, "orders.edit");
  const canCorrectPrice = can(me, "orders.correct_price");
  const [editing, setEditing] = useState<Order | null>(null);
  const [correctingPrice, setCorrectingPrice] = useState<Order | null>(null);
  const [fixating, setFixating] = useState<Order | null>(null);
  const archive = useOrderArchive(() => void onArchived());

  function items(order: Order): ActionMenuItem[] {
    // Пока машина на посту, заказ в архив не уходит: сервер откажет так же.
    const loading = ORDER_LOADING_STATUSES.includes(order.status);
    return [
      ...(canEdit ? [{ key: "edit", label: "Изменить заказ", icon: Pencil, onSelect: () => setEditing(order) }] : []),
      ...(canCorrectPrice
        ? [
            {
              key: "correct-price",
              label: "Корректировать стоимость",
              icon: CircleDollarSign,
              onSelect: () => setCorrectingPrice(order),
            },
          ]
        : []),
      ...(canEdit && canFixateOrder(order)
        ? [
            {
              key: "fixate",
              label: "Зафиксировать статус и оплату",
              icon: CalendarClock,
              onSelect: () => setFixating(order),
            },
          ]
        : []),
      ...(canEdit
        ? [
            {
              key: "archive",
              label: "В архив",
              icon: Archive,
              tone: "destructive" as const,
              disabled: loading,
              hint: loading ? "Сначала завершите или верните текущую погрузку" : undefined,
              onSelect: () => archive.open(order),
            },
          ]
        : []),
    ];
  }

  const dialogs = (
    <>
      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        eyebrow={editing ? `Работа · Заказ #${editing.id}` : "Работа · Заказ"}
        title="Изменить заказ"
        description="Позиции, цены, машина и дата прибытия. Изменения фиксируются в журнале."
        className="max-w-5xl"
        mobileFullscreen
      >
        {editing && (
          <OrderForm
            editing={editing}
            onCancel={() => setEditing(null)}
            onDone={() => {
              setEditing(null);
              void onChanged();
            }}
          />
        )}
      </Modal>
      <OrderPriceCorrectionModal
        order={correctingPrice}
        onClose={() => setCorrectingPrice(null)}
        onDone={() => {
          setCorrectingPrice(null);
          void onChanged();
        }}
      />
      <OrderFixationModal order={fixating} onClose={() => setFixating(null)} onChanged={onChanged} />
      <OrderArchiveDialog action={archive} />
    </>
  );

  return {
    items,
    /** Есть хоть одно действие — строке нужно меню «⋮». */
    available: canEdit || canCorrectPrice,
    dialogs,
  };
}
