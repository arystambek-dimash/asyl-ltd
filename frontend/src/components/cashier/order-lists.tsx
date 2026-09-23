"use client";
import type { TabDef } from "@/components/ui/tabs";
import type { Me } from "@/lib/types";
import { AwaitingPaymentRow } from "./awaiting-payment-row";
import { AwaitingShipmentRow, OverpaidOrderRow } from "./order-row";
import type { CashierQueue } from "./use-cashier-queue";

/** Списки заказов отдела в «Оплатах» кассы. */
export type OrderListKey = "awaiting" | "shipment" | "refund";

export const ORDER_LISTS: Record<OrderListKey, { label: string; empty: string }> = {
  awaiting: { label: "Ждут оплаты", empty: "Все отгруженные заказы оплачены или в долге." },
  shipment: { label: "К отгрузке", empty: "Все заказы, которые ждут отгрузки, оплачены." },
  refund: { label: "К возврату", empty: "Переплат нет." },
};

const ROWS = { awaiting: AwaitingPaymentRow, shipment: AwaitingShipmentRow, refund: OverpaidOrderRow };

export function orderListPage(q: CashierQueue, key: OrderListKey) {
  return key === "awaiting" ? q.awaitingPage : key === "shipment" ? q.shipmentPage : q.refundPage;
}

/** Вкладки списков со счётчиками; пока списки грузятся, счётчиков нет. */
export function orderListTabs(q: CashierQueue, keys: readonly OrderListKey[]): TabDef[] {
  return keys.map((key) => ({
    key,
    label: ORDER_LISTS[key].label,
    count: q.loading ? undefined : orderListPage(q, key).count,
  }));
}

export function OrderListRows({
  list,
  q,
  me,
  canOpenOrder,
  className,
}: {
  list: OrderListKey;
  q: CashierQueue;
  me: Me | null;
  canOpenOrder: boolean;
  className?: string;
}) {
  const Row = ROWS[list];
  return (
    <ul className={className}>
      {orderListPage(q, list).items.map((order) => (
        <Row key={order.id} order={order} q={q} me={me} canOpenOrder={canOpenOrder} />
      ))}
    </ul>
  );
}
