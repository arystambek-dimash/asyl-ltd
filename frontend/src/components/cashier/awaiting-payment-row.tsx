"use client";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { OrderPaymentActions } from "@/components/payments/order-payment-actions";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { Me, Order } from "@/lib/types";
import { formatCurrency, formatDateTime } from "@/lib/utils";
import { CashierOrderRow, paidOfTotal } from "./order-row";
import type { CashierQueue } from "./use-cashier-queue";

function remainingOf(order: Order): string {
  return order.remaining_amount ?? String(Number(order.total_amount) - Number(order.paid_total));
}

/** Почему заказ ждёт оплаты: клиент не выбрал способ, оплата не дошла или ещё идёт. */
export function awaitingReason(order: Order): { label: string; inProgress: boolean } {
  if ((order.pending_payments?.length ?? 0) > 0) return { label: "Оплата в процессе", inProgress: true };
  return {
    label: order.settlement_intent === "instant" ? "Оплата не завершена" : "Способ оплаты не выбран",
    inProgress: false,
  };
}

/**
 * Несогласованный долг в «Оплатах» кассы: остаток отгруженного заказа уже долг клиента, но клиент
 * не выбирал «в долг» — касса принимает оплату или согласует долг.
 * Оплату в процессе сначала подтверждают или отклоняют — долг поверх неё не согласуют.
 */
export function AwaitingPaymentRow({
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
  const [askDebt, setAskDebt] = useState(false);
  const reason = awaitingReason(order);
  const remaining = remainingOf(order);

  return (
    <CashierOrderRow
      order={order}
      amount={remaining}
      details={[order.shipped_at && `отгружен ${formatDateTime(order.shipped_at)}`, paidOfTotal(order)]}
      badge={<Badge tone={reason.inProgress ? "primary" : "warning"}>{reason.label}</Badge>}
      canOpenOrder={canOpenOrder}
    >
      <OrderPaymentActions order={order} me={me} onChanged={() => void q.reload()} />
      <Button
        size="sm"
        variant="outline"
        disabled={q.busy || reason.inProgress}
        title={reason.inProgress ? "Сначала подтвердите или отклоните оплату в процессе" : undefined}
        onClick={() => setAskDebt(true)}
      >
        В долг
      </Button>
      <ConfirmDialog
        open={askDebt}
        onClose={() => setAskDebt(false)}
        title={`Оставить заказ #${order.id} в долг?`}
        description={`${order.client_name ?? "Клиент"}: остаток ${formatCurrency(remaining, order.currency)} уже в «Долгах клиентов». Касса согласует долг — заказ уйдёт из «Ждут оплаты».`}
        confirmLabel="В долг"
        confirmVariant="default"
        busy={q.busy}
        error={askDebt ? q.error : ""}
        onConfirm={async () => {
          if (await q.moveToDebt(order)) setAskDebt(false);
        }}
      />
    </CashierOrderRow>
  );
}
