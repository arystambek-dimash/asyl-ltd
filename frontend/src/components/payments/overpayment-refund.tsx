"use client";
import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { PaymentRefundModal } from "@/components/transactions/payment-refund-modal";
import { Button } from "@/components/ui/button";
import { can } from "@/lib/can";
import { moneyCents } from "@/lib/debt-orders";
import { showSuccess } from "@/lib/toast";
import type { Me, Order, Payment, QrRefundState } from "@/lib/types";

/** Подтверждённые оплаты заказа, которые ещё можно вернуть, — свежие первыми. */
export function refundablePayments(order: Order): Payment[] {
  return (order.payments ?? [])
    .filter((row) => row.status === "confirmed" && moneyCents(row.available_for_refund ?? 0) > 0)
    .sort((a, b) => b.paid_at.localeCompare(a.paid_at));
}

/**
 * «Оформить возврат» переплаты заказа (сервер: `overpaid_amount`). Подтверждённые
 * деньги не меняются при уменьшении количества или цены — излишек возвращают
 * отдельной операцией по одной из оплат. Сумма по умолчанию — не больше
 * переплаты. Карточка заказа и «Касса → Оплаты → К возврату».
 *
 * Возврат по Kaspi QR ждёт покупателя, а переплата после его начала исчезает —
 * вместе с кнопкой. Окно ссылки держит родитель (`useQrRefundWindow`), кнопка
 * лишь передаёт его через `onQrRefund`.
 */
export function OverpaymentRefundButton({
  order,
  me,
  onChanged,
  onQrRefund,
  className,
}: {
  order: Order;
  me: Me | null;
  onChanged: () => unknown;
  onQrRefund: (payment: Payment, initial: QrRefundState) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const overpaid = moneyCents(order.overpaid_amount ?? 0);
  const payments = refundablePayments(order);
  if (overpaid <= 0 || payments.length === 0) return null;
  if (!can(me, "payments.confirm"))
    return (
      <span className="text-xs text-[var(--muted-foreground)]">
        Вернуть может сотрудник с правом подтверждения оплат.
      </span>
    );

  async function refunded(payment: Payment, initial: QrRefundState | null) {
    setOpen(false);
    if (initial) onQrRefund(payment, initial);
    else showSuccess(`Возврат по заказу #${order.id} оформлен`);
    await onChanged();
  }

  return (
    <>
      <Button size="sm" variant="outline" className={className} onClick={() => setOpen(true)}>
        <RotateCcw className="size-4" /> Вернуть переплату
      </Button>
      {open && (
        <PaymentRefundModal
          payment={payments[0]}
          choices={payments}
          amountFor={(row) => String(Math.min(overpaid, moneyCents(row.available_for_refund ?? 0)) / 100)}
          onClose={() => setOpen(false)}
          onRefunded={refunded}
        />
      )}
    </>
  );
}
