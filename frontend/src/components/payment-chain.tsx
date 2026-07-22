"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatCurrency } from "@/lib/utils";
import {
  CASHIER_PAYMENT_METHOD_LABELS,
  CASHIER_PAYMENT_METHODS,
  PAYMENT_STAGE_LABELS,
  PAYMENT_STAGE_TONE,
} from "@/lib/constants";
import { HandCoins, ReceiptText } from "lucide-react";
import type { Me, Order, Payment } from "@/lib/types";

/** Приём оплат открывается после фактической отгрузки для любого отдела. */
export function paymentOpen(order: Order): boolean {
  return order.status === "shipped";
}

export function PaymentStageBadge({ status }: { status: string }) {
  return (
    <Badge tone={PAYMENT_STAGE_TONE[status] ?? "muted"} dot>
      {PAYMENT_STAGE_LABELS[status] ?? status}
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
            {s.at ? ` · ${new Date(s.at).toLocaleString("ru-RU")}` : ""}
          </span>
        ))}
      </div>
    </details>
  );
}

/**
 * Оплаты заказа в цепочке подтверждения с действиями по правам:
 * приём (payments.create) → подтверждение бухгалтером-кассой (payments.confirm).
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
                <div className="font-semibold">{p.status === "received" ? "Проверьте оплату" : "Ожидаем оплату"}</div>
                <div className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                  <span className="font-medium tabular-nums text-[var(--foreground)]">
                    {formatCurrency(p.amount, order.currency)}
                  </span>
                  {" · "}
                  {CASHIER_PAYMENT_METHOD_LABELS[p.method] || p.method_label || p.method}
                </div>
              </div>
            </div>
            <PaymentStageBadge status={p.status} />
          </div>
          <div className="flex flex-wrap gap-2">
            {p.status === "requested" && can(me, "payments.create") && (
              <Button size="sm" disabled={busy} onClick={() => act(`/orders/${order.id}/payments/${p.id}/receive/`)}>
                Отметить получение
              </Button>
            )}
            {p.status === "received" && can(me, "payments.confirm") && (
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

/**
 * Кнопки старта цепочки: «Запросить оплату» (счёт выставлен) и
 * «Принять оплату» (деньги получены с выезда). Требует payments.create.
 */
export function AddPaymentActions({
  order,
  me,
  onChanged,
  mode = "both",
}: {
  order: Order;
  me: Me | null;
  onChanged: () => void;
  mode?: "both" | "request" | "receive";
}) {
  const [stage, setStage] = useState<"requested" | "received" | null>(null);
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("cash");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!can(me, "payments.create") || !paymentOpen(order)) return null;
  const remaining = Number(order.remaining_amount ?? Number(order.total_amount) - Number(order.paid_total));
  if (remaining <= 0) return null;

  function open(s: "requested" | "received") {
    setStage(s);
    setAmount(String(remaining));
    setMethod("cash");
    setError("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post(`/orders/${order.id}/payments/`, { amount, method, stage });
      setStage(null);
      onChanged();
    } catch (err) {
      setError(apiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {mode !== "receive" && (
          <Button size="sm" variant="outline" onClick={() => open("requested")}>
            <ReceiptText className="size-4" /> Запросить оплату
          </Button>
        )}
        {mode !== "request" && (
          <Button size="sm" onClick={() => open("received")}>
            <HandCoins className="size-4" /> Принять оплату
          </Button>
        )}
      </div>
      <Modal
        open={stage !== null}
        onClose={() => setStage(null)}
        eyebrow={`Заказ #${order.id} · ${order.client_name ?? ""}`}
        title={stage === "requested" ? "Запросить оплату" : "Принять оплату"}
        description={
          stage === "requested"
            ? "Клиенту выставлен счёт. После поступления кассир вручную подтвердит получение."
            : "Оплата добавится в очередь и будет учтена только после ручного подтверждения кассиром."
        }
        className="max-w-sm"
      >
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="grid gap-2">
            <Label htmlFor="payment-amount">Сумма (остаток {formatCurrency(String(remaining), order.currency)})</Label>
            <Input
              id="payment-amount"
              type="number"
              min="1"
              step="0.01"
              value={amount}
              autoFocus
              onChange={(e) => setAmount(e.target.value)}
              required
            />
            <p className="text-xs text-[var(--muted-foreground)]">
              Валюта оплаты закреплена заказом: {order.currency === "USD" ? "USD ($)" : "KZT (₸)"}.
            </p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="payment-method">Способ</Label>
            <Select id="payment-method" value={method} onChange={(e) => setMethod(e.target.value)}>
              {CASHIER_PAYMENT_METHODS.map((key) => (
                <option key={key} value={key}>
                  {CASHIER_PAYMENT_METHOD_LABELS[key]}
                </option>
              ))}
            </Select>
          </div>
          {error && (
            <p role="alert" className="text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setStage(null)}>
              Отмена
            </Button>
            <Button type="submit" disabled={busy || Number(amount) <= 0}>
              {busy ? "Сохранение…" : stage === "requested" ? "Запросить" : "Принять"}
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
