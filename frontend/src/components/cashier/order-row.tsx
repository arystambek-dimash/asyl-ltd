"use client";
import { OrderPaymentActions } from "@/components/payments/order-payment-actions";
import { OverpaymentRefundButton } from "@/components/payments/overpayment-refund";
import { StatusBadge } from "@/components/status-badge";
import type { Me, Order } from "@/lib/types";
import { cn, formatCurrency } from "@/lib/utils";
import { OrderDepartmentBadge } from "@/components/ui/department-badge";
import { OrderRef } from "@/components/orders/order-ref";
import type { CashierQueue } from "./use-cashier-queue";

/** «оплачено 100 ₸ из 300 ₸» — только если часть уже оплачена. */
export function paidOfTotal(order: Order): string | null {
  if (Number(order.paid_total) <= 0) return null;
  return `оплачено ${formatCurrency(order.paid_total, order.currency)} из ${formatCurrency(order.total_amount, order.currency)}`;
}

/**
 * Строка в «Оплатах» кассы: сумма крупно, заказ (ссылкой, если кассе можно его
 * открыть), клиент и подробности, справа — бейджи, ниже — действия.
 */
export function CashierRow({
  orderId,
  amount,
  currency,
  amountClassName,
  details,
  badges,
  canOpenOrder,
  children,
}: {
  orderId: number;
  /** Крупная сумма строки: остаток к оплате, переплата или сумма оплаты. */
  amount: string;
  currency?: string;
  amountClassName?: string;
  details: (string | null | undefined | false)[];
  badges: React.ReactNode;
  canOpenOrder: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className={cn("text-[17px] font-bold tabular-nums", amountClassName)}>
            {formatCurrency(amount, currency)}
          </div>
          <div className="mt-0.5 text-[13px] text-[var(--muted-foreground)]">
            <OrderRef
              id={orderId}
              canOpen={canOpenOrder}
              back="/accounting?view=confirm"
              linkClassName="underline-offset-2"
            >
              Заказ #{orderId}
            </OrderRef>
            {details.filter(Boolean).map((part) => ` · ${part}`)}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">{badges}</div>
      </div>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </li>
  );
}

/**
 * «К отгрузке»: заказ ждёт отгрузки и оплачен не полностью. Касса берёт
 * предоплату — только деньгами у кассы (сервер не открывает счёт на телефон
 * и Kaspi QR до отгрузки). Долга ещё нет, поэтому и «В долг» здесь нет.
 */
export function AwaitingShipmentRow({
  order,
  q,
  me,
  canOpenOrder,
}: {
  order: Order;
  q: CashierQueue;
  me: Me | null;
  canOpenOrder: boolean;
}) {
  return (
    <CashierRow
      orderId={order.id}
      amount={order.remaining_amount}
      currency={order.currency}
      details={[order.client_name, paidOfTotal(order)]}
      badges={
        <>
          <StatusBadge status={order.status} />
          <OrderDepartmentBadge order={order} />
        </>
      }
      canOpenOrder={canOpenOrder}
    >
      <OrderPaymentActions order={order} me={me} onChanged={() => void q.reload()} />
    </CashierRow>
  );
}

/** «К возврату»: подтверждённых денег больше суммы заказа — излишек вернуть клиенту. */
export function OverpaidOrderRow({
  order,
  q,
  me,
  canOpenOrder,
}: {
  order: Order;
  q: CashierQueue;
  me: Me | null;
  canOpenOrder: boolean;
}) {
  return (
    <CashierRow
      orderId={order.id}
      amount={order.overpaid_amount ?? "0"}
      currency={order.currency}
      amountClassName="text-[var(--warning)]"
      details={[order.client_name, paidOfTotal(order)]}
      badges={
        <>
          <StatusBadge status={order.status} />
          <OrderDepartmentBadge order={order} />
        </>
      }
      canOpenOrder={canOpenOrder}
    >
      <OverpaymentRefundButton order={order} me={me} onChanged={q.reload} onQrRefund={q.qrRefund.start} />
    </CashierRow>
  );
}
