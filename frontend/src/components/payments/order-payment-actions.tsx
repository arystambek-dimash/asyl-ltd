"use client";
import { useState } from "react";
import { Banknote, HandCoins, QrCode, Send, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { paymentOpen } from "@/components/payment-chain";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { availableCents, moneyCents } from "@/lib/debt-orders";
import type { Me, Order } from "@/lib/types";
import { cn, formatCurrency } from "@/lib/utils";

type Flow = "receive" | "remote";
type ReceiveMethod = "cash" | "kaspi" | "remote";

/** «Удалённая оплата» — отметка о деньгах, полученных раньше и не через кассу:
 * счёт она не выставляет и новую оплату не начинает, только закрывает долг. */
const METHOD_OPTIONS: { key: ReceiveMethod; label: string; hint?: string; icon: typeof Banknote }[] = [
  { key: "cash", label: "Наличные", icon: Banknote },
  { key: "kaspi", label: "Kaspi QR", icon: QrCode },
  { key: "remote", label: "Удалённая оплата", hint: "клиент оплатил раньше", icon: Smartphone },
];

/** Сумма к оплате: положительная, с точностью до тиына и не больше доступного остатка. */
export function paymentAmountProblem(amount: string, maxCents: number): string {
  const value = Number(amount);
  if (!amount.trim() || !Number.isFinite(value) || value <= 0) return "Укажите сумму больше нуля.";
  if (Math.abs(value * 100 - Math.round(value * 100)) > 1e-7) return "Сумма указывается с точностью до тиына.";
  if (moneyCents(value) > maxCents) return "Сумма больше остатка к оплате.";
  return "";
}

/**
 * Единственное место приёма денег по заказу в CRM: «Принять оплату» — деньги
 * получены на месте (наличные или Kaspi QR), долг уменьшается сразу;
 * «Отправить удалённый счёт» — счёт Kaspi клиенту на телефон, долг уменьшится,
 * когда клиент оплатит.
 */
export function OrderPaymentActions({
  order,
  me,
  onChanged,
  clientPhone,
  blockedReason,
  className,
}: {
  order: Order;
  me: Me | null;
  /** После успешной оплаты: текст для уведомления на странице. */
  onChanged: (notice: string) => void;
  /** Телефон клиента, если в самом заказе его нет. */
  clientPhone?: string | null;
  /** Почему оплата сейчас закрыта (например, окно оплаты магазина). */
  blockedReason?: string | null;
  className?: string;
}) {
  const [flow, setFlow] = useState<Flow | null>(null);
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<ReceiveMethod>("cash");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const maxCents = availableCents(order);
  if (!can(me, "payments.create") || !paymentOpen(order) || maxCents <= 0) return null;

  const kzt = order.currency === "KZT";
  const available = formatCurrency(String(maxCents / 100), order.currency);
  const amountProblem = paymentAmountProblem(amount, maxCents);
  const phoneOk = [10, 11].includes(phone.replace(/\D/g, "").length);
  const canSubmit = !busy && !amountProblem && (flow !== "remote" || phoneOk);

  function open(next: Flow) {
    setFlow(next);
    setAmount(String(maxCents / 100));
    setMethod("cash");
    setPhone(order.client_phone || clientPhone || "");
    setError("");
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit || flow === null) return;
    setBusy(true);
    setError("");
    try {
      const sum = formatCurrency(amount, order.currency);
      if (flow === "receive") {
        await api.post(`/orders/${order.id}/payments/`, { amount, method, stage: "received" });
        onChanged(`Оплата ${sum} по заказу #${order.id} принята — долг уменьшен.`);
      } else {
        await api.post(`/orders/${order.id}/payments/`, {
          amount,
          method: "invoice",
          stage: "requested",
          channel: "remote",
          phone_number: phone,
        });
        onChanged(`Счёт на ${sum} по заказу #${order.id} отправлен в Kaspi — долг уменьшится после оплаты.`);
      }
      setFlow(null);
    } catch (err) {
      // Ошибка остаётся в окне: страница под оверлеем её не покажет.
      setError(apiError(err));
    } finally {
      setBusy(false);
    }
  }

  const blocked = Boolean(blockedReason);

  return (
    <>
      <div className={cn("flex flex-wrap items-center gap-2", className)}>
        <Button size="sm" disabled={blocked} title={blockedReason ?? undefined} onClick={() => open("receive")}>
          <HandCoins className="size-4" /> Принять оплату
        </Button>
        {kzt && (
          <Button
            size="sm"
            variant="outline"
            disabled={blocked}
            title={blockedReason ?? undefined}
            onClick={() => open("remote")}
          >
            <Send className="size-4" /> Отправить удалённый счёт
          </Button>
        )}
        {blockedReason && <span className="text-xs text-[var(--warning)]">{blockedReason}</span>}
      </div>
      <Modal
        open={flow !== null}
        onClose={() => !busy && setFlow(null)}
        eyebrow={`Заказ #${order.id}${order.client_name ? ` · ${order.client_name}` : ""}`}
        title={flow === "remote" ? "Отправить удалённый счёт" : "Принять оплату"}
        description={
          flow === "remote"
            ? "Счёт придёт клиенту в Kaspi на телефон. Долг уменьшится сам, когда клиент оплатит."
            : "Деньги уже получены — долг уменьшится сразу. Ничего клиенту не отправляется."
        }
        className="max-w-sm"
      >
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="grid gap-2">
            <Label htmlFor={`payment-amount-${order.id}`}>Сумма</Label>
            <Input
              id={`payment-amount-${order.id}`}
              type="number"
              min="0.01"
              step="0.01"
              inputMode="decimal"
              className="text-base"
              value={amount}
              autoFocus
              onChange={(event) => setAmount(event.target.value)}
            />
            <div className="flex items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]">
              <span>Остаток к оплате: {available}</span>
              {amount !== String(maxCents / 100) && (
                <button
                  type="button"
                  className="font-medium text-[var(--foreground)] underline-offset-2 hover:underline"
                  onClick={() => setAmount(String(maxCents / 100))}
                >
                  Весь остаток
                </button>
              )}
            </div>
            {amount && amountProblem && <p className="text-xs text-[var(--destructive)]">{amountProblem}</p>}
          </div>

          {flow === "receive" && kzt && (
            <div className="grid gap-2">
              <Label>Способ</Label>
              <div className="grid grid-cols-2 gap-2">
                {METHOD_OPTIONS.map(({ key, label, hint, icon: Icon }) => (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={method === key}
                    onClick={() => setMethod(key)}
                    className={cn(
                      "flex items-center gap-2 rounded-lg border px-3 py-2.5 text-left text-sm font-medium transition-colors",
                      hint && "col-span-2",
                      method === key
                        ? "border-[var(--foreground)] bg-[var(--muted)]"
                        : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--foreground)]/40",
                    )}
                  >
                    <Icon className="size-4 shrink-0" />
                    <span>
                      {label}
                      {hint && <span className="ml-1 text-xs font-normal opacity-70">· {hint}</span>}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {flow === "remote" && (
            <div className="grid gap-2">
              <Label htmlFor={`payment-phone-${order.id}`}>Телефон клиента в Kaspi</Label>
              <Input
                id={`payment-phone-${order.id}`}
                type="tel"
                inputMode="tel"
                className="text-base"
                placeholder="8 700 000 00 00"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
              />
              {phone && !phoneOk && <p className="text-xs text-[var(--destructive)]">Проверьте номер телефона.</p>}
            </div>
          )}

          {error && (
            <p role="alert" className="text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" disabled={busy} onClick={() => setFlow(null)}>
              Отмена
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {busy ? "Сохранение…" : flow === "remote" ? "Отправить счёт" : "Принять"}
            </Button>
          </div>
        </form>
      </Modal>
    </>
  );
}
