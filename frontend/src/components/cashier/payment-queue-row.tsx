"use client";
import { PaymentStageBadge } from "@/components/payment-chain";
import { Button } from "@/components/ui/button";
import { DepartmentBadge } from "@/components/ui/department-badge";
import type { PaymentQueueItem } from "@/lib/types";
import { CashierRow } from "./order-row";
import type { CashierQueue } from "./use-cashier-queue";

/** Оплата к подтверждению: заявленную отмечают поступившей, поступившую — подтверждают или отклоняют. */
export function PaymentQueueRow({
  p,
  q,
  canOpenOrder,
}: {
  p: PaymentQueueItem;
  q: CashierQueue;
  canOpenOrder: boolean;
}) {
  return (
    <CashierRow
      orderId={p.order}
      amount={p.amount}
      currency={p.currency}
      details={[p.client_name, p.method_label, p.store_name, p.received_by_name && `принял ${p.received_by_name}`]}
      badges={
        <>
          <PaymentStageBadge payment={p} />
          <DepartmentBadge name={p.department_name} color={p.department_color} />
        </>
      }
      canOpenOrder={canOpenOrder}
    >
      <Button
        className="flex-1"
        disabled={q.busy}
        onClick={() => (p.status === "requested" ? q.receivePayment(p) : q.confirmPayment(p))}
      >
        {p.status === "requested" ? "Оплата поступила" : "Подтвердить получение"}
      </Button>
      <Button variant="ghost" disabled={q.busy} onClick={() => q.rejectPayment(p)}>
        Отклонить
      </Button>
    </CashierRow>
  );
}
