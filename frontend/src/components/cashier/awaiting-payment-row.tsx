"use client";
import Link from "next/link";
import { useState } from "react";
import { AddPaymentActions } from "@/components/payment-chain";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { withBack } from "@/lib/navigation";
import type { Me, Order } from "@/lib/types";
import { formatCurrency, formatDateTime } from "@/lib/utils";
import { OrderDepartmentBadge } from "./department-badge";
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
 * Отгруженный заказ без долга в «Оплатах» кассы: принять оплату или перевести в долг.
 * Оплату в процессе сначала подтверждают или отклоняют — долг поверх неё не ставится.
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
  const remaining = formatCurrency(remainingOf(order), order.currency);
  const partlyPaid = Number(order.paid_total) > 0;

  return (
    <li className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[17px] font-bold tabular-nums">{remaining}</div>
          <div className="mt-0.5 text-[13px] text-[var(--muted-foreground)]">
            {canOpenOrder ? (
              <Link
                href={withBack(`/orders/${order.id}`, "/accounting?view=confirm")}
                className="underline-offset-2 hover:underline"
              >
                Заказ #{order.id}
              </Link>
            ) : (
              <span>Заказ #{order.id}</span>
            )}
            {" · "}
            {order.client_name}
            {order.shipped_at ? ` · отгружен ${formatDateTime(order.shipped_at)}` : ""}
            {partlyPaid
              ? ` · оплачено ${formatCurrency(order.paid_total, order.currency)} из ${formatCurrency(order.total_amount, order.currency)}`
              : ""}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <Badge tone={reason.inProgress ? "primary" : "warning"}>{reason.label}</Badge>
          <OrderDepartmentBadge order={order} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <AddPaymentActions order={order} me={me} mode="receive" onChanged={() => void q.reload()} />
        <Button
          size="sm"
          variant="outline"
          disabled={q.busy || reason.inProgress}
          title={reason.inProgress ? "Сначала подтвердите или отклоните оплату в процессе" : undefined}
          onClick={() => setAskDebt(true)}
        >
          В долг
        </Button>
      </div>
      <ConfirmDialog
        open={askDebt}
        onClose={() => setAskDebt(false)}
        title={`Перевести заказ #${order.id} в долг?`}
        description={`${order.client_name ?? "Клиент"}: остаток ${remaining} появится в «Долгах клиентов».`}
        confirmLabel="В долг"
        confirmVariant="default"
        busy={q.busy}
        error={askDebt ? q.error : ""}
        onConfirm={async () => {
          if (await q.moveToDebt(order)) setAskDebt(false);
        }}
      />
    </li>
  );
}
