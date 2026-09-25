"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatCurrency, formatDateTime } from "@/lib/utils";
import { PAYMENT_STAGE_TONE } from "@/lib/constants";
import { ReceiptText } from "lucide-react";
import type { PaidMethodPart } from "@/components/transactions/paid-method-summary";
import type { Me, Order, Payment } from "@/lib/types";

/** Этап кассовой оплаты: тон по статусу, подпись с бэка (status_label). */
export function PaymentStageBadge({
  payment,
  dot = true,
}: {
  payment: { status: string; status_label: string };
  dot?: boolean;
}) {
  return (
    <Badge tone={PAYMENT_STAGE_TONE[payment.status] ?? "muted"} dot={dot}>
      {payment.status_label}
    </Badge>
  );
}

function StageTrace({ p }: { p: Payment }) {
  const steps = [
    { label: "Создана", by: p.recorded_by_name, at: p.paid_at },
    { label: "Принята", by: p.received_by_name, at: p.received_at },
    { label: "Подтверждена", by: p.confirmed_by_name, at: p.confirmed_at },
  ].filter((s) => s.by || s.at);
  return (
    <details className="text-[11px] text-[var(--muted-foreground)]">
      <summary className="w-fit cursor-pointer select-none hover:text-[var(--foreground)]">История оплаты</summary>
      <div className="mt-1.5 flex flex-col gap-0.5 border-l pl-2.5">
        {steps.map((s) => (
          <span key={s.label}>
            {s.label}: {s.by ?? "—"}
            {s.at ? ` · ${formatDateTime(s.at)}` : ""}
          </span>
        ))}
      </div>
    </details>
  );
}

/** Чистая сумма оплаты — за вычетом возвратов, как Payment.net_amount на бэке. */
export function paymentNetAmount(payment: Pick<Payment, "amount" | "refunded_amount">): number {
  return Math.max(0, Number(payment.amount) - Number(payment.refunded_amount ?? 0));
}

/** Подтверждённые оплаты заказа, свёрнутые по способу, — части для PaidMethodSummary. */
export function paidByMethod(order: Order): (PaidMethodPart & { amount: number })[] {
  const totals = new Map<string, PaidMethodPart & { amount: number }>();
  for (const payment of order.payments ?? []) {
    if (payment.status !== "confirmed") continue;
    // Та же чистая сумма, из которой сложен paid_total: возврат уменьшает
    // вклад способа, иначе разбивка не сойдётся с итогом заказа.
    const net = paymentNetAmount(payment);
    if (net <= 0) continue;
    const part = totals.get(payment.method) ?? {
      currency: order.currency,
      method: payment.method,
      label: payment.method_label,
      amount: 0,
    };
    part.amount += net;
    totals.set(payment.method, part);
  }
  return [...totals.values()].sort((a, b) => b.amount - a.amount);
}

/**
 * Оплаты заказа в цепочке подтверждения с действиями по правам:
 * providerless-заявку принимает и подтверждает касса (payments.confirm),
 * а провайдерский счёт закрывается только уведомлением платёжного сервиса.
 */
export function PaymentChain({ order, me, onChanged }: { order: Order; me: Me | null; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const payments = order.pending_payments ?? [];
  if (payments.length === 0) return null;

  async function act(url: string) {
    setBusy(true);
    setError("");
    try {
      await api.post(url);
      onChanged();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      {payments.map((p) => (
        <div key={p.id} className="flex flex-col gap-3 rounded-lg border bg-[var(--muted)]/15 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex min-w-0 items-start gap-3">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[var(--warning)]/12 text-[var(--warning)]">
                <ReceiptText className="size-4" />
              </span>
              <div>
                <div className="font-semibold">
                  {p.confirmation_mode === "automatic"
                    ? "Ожидаем подтверждение сервиса"
                    : p.status === "received"
                      ? "Проверьте оплату"
                      : "Ожидаем оплату"}
                </div>
                <div className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                  <span className="font-medium tabular-nums text-[var(--foreground)]">
                    {formatCurrency(p.amount, order.currency)}
                  </span>
                  {" · "}
                  {p.method_label}
                </div>
              </div>
            </div>
            <PaymentStageBadge payment={p} />
          </div>
          <div className="flex flex-wrap gap-2">
            {p.status === "requested" && p.confirmation_mode !== "automatic" && can(me, "payments.confirm") && (
              <Button size="sm" disabled={busy} onClick={() => act(`/orders/${order.id}/payments/${p.id}/receive/`)}>
                Оплата поступила
              </Button>
            )}
            {p.status === "received" && p.confirmation_mode !== "automatic" && can(me, "payments.confirm") && (
              <Button size="sm" disabled={busy} onClick={() => act(`/orders/${order.id}/payments/${p.id}/confirm/`)}>
                Подтвердить получение
              </Button>
            )}
            {can(me, "payments.confirm") && (
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => act(`/orders/${order.id}/payments/${p.id}/reject/`)}
              >
                Отклонить
              </Button>
            )}
          </div>
          <StageTrace p={p} />
        </div>
      ))}
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
    </div>
  );
}
