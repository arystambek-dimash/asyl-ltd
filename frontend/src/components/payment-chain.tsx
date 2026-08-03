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
import { cn, formatCurrency, formatDateTime } from "@/lib/utils";
import {
  CASHIER_PAYMENT_METHODS,
  PAYMENT_METHOD_LABELS,
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
            {s.at ? ` · ${formatDateTime(s.at)}` : ""}
          </span>
        ))}
      </div>
    </details>
  );
}

/** Подтверждённые оплаты заказа, свёрнутые по способу: [способ, сумма]. */
export function paidByMethod(order: Order): [string, number][] {
  const totals = new Map<string, number>();
  for (const payment of order.payments ?? []) {
    if (payment.status !== "confirmed") continue;
    // Та же чистая сумма, из которой сложен paid_total: возврат уменьшает
    // вклад способа, иначе разбивка не сойдётся с итогом заказа.
    const net = Number(payment.amount) - Number(payment.refunded_amount ?? 0);
    if (net <= 0) continue;
    totals.set(payment.method, (totals.get(payment.method) ?? 0) + net);
  }
  return [...totals.entries()].sort((a, b) => b[1] - a[1]);
}

/**
 * Из чего сложилась оплата: «300 000 ₸ наличными · 400 000 ₸ QR».
 *
 * Итоговая сумма сама по себе не отвечает на вопрос кассира «чем платили»,
 * а при смешанной оплате это и есть главное, что нужно видеть сразу.
 * Один способ показывать не нужно — он уже подписан рядом с суммой.
 */
export function PaidMethodBreakdown({ order, className = "" }: { order: Order; className?: string }) {
  const parts = paidByMethod(order);
  if (parts.length < 2) return null;
  return (
    // Сумма и её способ переносятся только вместе: «300 000 ₸» отдельно от
    // «Наличные» читается как другая величина. Перенос допустим лишь между
    // способами, поэтому разделитель живёт внутри своей пары.
    <div className={`text-[var(--muted-foreground)] ${className}`}>
      {parts.map(([method, amount], index) => (
        <span key={method} className="whitespace-nowrap">
          {index > 0 && <span className="px-1.5">·</span>}
          <span className="font-medium tabular-nums text-[var(--foreground)]">
            {formatCurrency(String(amount), order.currency)}
          </span>{" "}
          {PAYMENT_METHOD_LABELS[method] || method}
        </span>
      ))}
    </div>
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
                  {PAYMENT_METHOD_LABELS[p.method] || p.method_label || p.method}
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
  // Канал счёта: remote — онлайн-счёт провайдера на телефон, document — наш
  // PDF, который клиент скачивает в портале, а подтверждает касса вручную.
  const [channel, setChannel] = useState<"remote" | "document">("remote");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!can(me, "payments.create") || !paymentOpen(order)) return null;
  const remaining = Number(order.remaining_amount ?? Number(order.total_amount) - Number(order.paid_total));
  if (remaining <= 0) return null;

  const isInvoice = method === "invoice";
  const isRemoteInvoice = isInvoice && channel === "remote";

  function open(s: "requested" | "received") {
    setStage(s);
    setAmount(String(remaining));
    setMethod("cash");
    setChannel("remote");
    setPhone(order.client_phone ?? "");
    setError("");
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.post(`/orders/${order.id}/payments/`, {
        amount,
        method,
        stage,
        // Канал имеет смысл только для счёта: для наличных и QR бэкенд его
        // игнорирует, и лишнее поле только запутало бы журнал.
        ...(isInvoice ? { channel } : {}),
        ...(isRemoteInvoice ? { phone_number: phone } : {}),
      });
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
        className={isInvoice ? "max-w-md" : "max-w-sm"}
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
                  {PAYMENT_METHOD_LABELS[key]}
                </option>
              ))}
            </Select>
          </div>
          {isInvoice && (
            <div className="grid gap-2">
              <Label>Как выставить счёт</Label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  aria-pressed={channel === "remote"}
                  onClick={() => setChannel("remote")}
                  className={cn(
                    "rounded-lg border px-3 py-2 text-left transition-colors",
                    channel === "remote"
                      ? "border-[var(--foreground)] bg-[var(--muted)]"
                      : "border-[var(--border)] hover:border-[var(--foreground)]/40",
                  )}
                >
                  <div className="text-sm font-medium">Счёт клиенту</div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Придёт на телефон, подтвердится сам</div>
                </button>
                <button
                  type="button"
                  aria-pressed={channel === "document"}
                  onClick={() => setChannel("document")}
                  className={cn(
                    "rounded-lg border px-3 py-2 text-left transition-colors",
                    channel === "document"
                      ? "border-[var(--foreground)] bg-[var(--muted)]"
                      : "border-[var(--border)] hover:border-[var(--foreground)]/40",
                  )}
                >
                  <div className="text-sm font-medium">Наш PDF-счёт</div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">
                    Клиент скачает в портале, подтвердит касса
                  </div>
                </button>
              </div>
              {isRemoteInvoice ? (
                <Input
                  inputMode="tel"
                  aria-label="Телефон для счёта на оплату"
                  placeholder="Телефон клиента для счёта"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
              ) : (
                <p className="text-xs text-[var(--muted-foreground)]">
                  Клиенту придёт уведомление в портале со ссылкой на счёт.
                </p>
              )}
            </div>
          )}
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
