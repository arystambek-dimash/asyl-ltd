"use client";
import { useEffect, useRef, useState } from "react";
import { Banknote, HandCoins, QrCode, Send, Smartphone } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { availableCents } from "@/lib/debt-orders";
import { paymentAmountError } from "@/lib/payment-amount";
import { isKaspiInvoicePhone } from "@/lib/phone";
import type { Me, Order } from "@/lib/types";
import { cn, formatCurrency } from "@/lib/utils";

type Flow = "receive" | "remote";
export type ReceiveMethod = "cash" | "kaspi" | "remote";

/** Способы «Принять оплату» — деньги уже у кассы, поэтому ими берут и предоплату.
 * `kaspi` — оплата по своему терминалу кассы (запрос без `channel`), подпись та же,
 * что у способа в labels.py; Kaspi QR через ApiPay выставляет только POS кассы.
 * «Удалённая оплата» — отметка о деньгах, полученных раньше и не через кассу:
 * счёт она не выставляет и новую оплату не начинает, только закрывает остаток. */
export const RECEIVE_METHOD_OPTIONS: {
  key: ReceiveMethod;
  label: string;
  hint?: string;
  icon: typeof Banknote;
}[] = [
  { key: "cash", label: "Наличные", icon: Banknote },
  { key: "kaspi", label: "QR", icon: QrCode },
  { key: "remote", label: "Удалённая оплата", hint: "клиент оплатил раньше", icon: Smartphone },
];

/**
 * Способы приёма из открытых сервером (`payment_open_methods`: статус и валюта
 * заказа). Без `open` новый заказ в форме ещё не сохранён — тогда способы по
 * валюте: Kaspi и удалённая оплата только в тенге, как проверяет сервер.
 */
export function receiveMethods(currency: string, open?: readonly string[]): ReceiveMethod[] {
  return RECEIVE_METHOD_OPTIONS.map(({ key }) => key).filter((key) =>
    open ? open.includes(key) : currency === "KZT" || key === "cash",
  );
}

/** «Принять оплату»: деньги уже у кассы, оплата закрывается сразу (и как предоплата до отгрузки). */
export function receivePayment(orderId: number, { amount, method }: { amount: string; method: ReceiveMethod }) {
  return api.post(`/orders/${orderId}/payments/`, { amount, method });
}

/** Выбор способа приёма денег: одна кнопка на способ, как в окне «Принять оплату». */
export function ReceiveMethodPicker({
  methods,
  value,
  onChange,
}: {
  methods: readonly ReceiveMethod[];
  value: ReceiveMethod;
  onChange: (method: ReceiveMethod) => void;
}) {
  const options = RECEIVE_METHOD_OPTIONS.filter(({ key }) => methods.includes(key));
  return (
    <div className="grid grid-cols-2 gap-2">
      {options.map(({ key, label, hint, icon: Icon }) => (
        <button
          key={key}
          type="button"
          aria-pressed={value === key}
          onClick={() => onChange(key)}
          className={cn(
            "flex items-center gap-2 rounded-lg border px-3 py-2.5 text-left text-sm font-medium transition-colors",
            hint && "col-span-2",
            value === key
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
  );
}

/** Открыть «Принять оплату» сразу — например, когда форма заказа не смогла провести оплату. */
export interface PaymentAutoOpen {
  method: string;
  amount: string;
  /** Почему окно открылось само: показывается в нём как ошибка. */
  notice: string;
}

/**
 * Единственное место приёма денег по заказу в CRM: «Принять оплату» — деньги
 * получены на месте (наличные или Kaspi на кассе), долг уменьшается сразу;
 * «Отправить удалённый счёт» — счёт Kaspi клиенту на телефон, долг уменьшится,
 * когда клиент оплатит.
 *
 * Когда и какими способами можно принять деньги, решает сервер (`payment_open`,
 * `payment_open_methods`, `payment_request_open`): до отгрузки — только
 * предоплата деньгами у кассы, счёт на телефон — после отгрузки.
 */
export function OrderPaymentActions({
  order,
  me,
  onChanged,
  clientPhone,
  blockedReason,
  className,
  autoOpen,
  onAutoOpened,
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
  /** Открыть окно приёма сразу с этими способом и суммой (один раз). */
  autoOpen?: PaymentAutoOpen | null;
  /** Окно открылось по `autoOpen` — страница может убрать признак из адреса. */
  onAutoOpened?: () => void;
}) {
  const [flow, setFlow] = useState<Flow | null>(null);
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState<ReceiveMethod>("cash");
  const [phone, setPhone] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const maxCents = availableCents(order);
  const methods = receiveMethods(order.currency, order.payment_open_methods ?? []);
  const visible = can(me, "payments.create") && Boolean(order.payment_open) && maxCents > 0 && methods.length > 0;
  const autoOpened = useRef(false);
  useEffect(() => {
    if (!autoOpen || !visible || autoOpened.current) return;
    autoOpened.current = true;
    setFlow("receive");
    setAmount(autoOpen.amount || String(maxCents / 100));
    setMethod(methods.find((key) => key === autoOpen.method) ?? methods[0]);
    setPhone("");
    setError(autoOpen.notice);
    onAutoOpened?.();
  }, [autoOpen, visible, maxCents, methods, onAutoOpened]);
  if (!visible) return null;

  const remoteInvoice = Boolean(order.payment_request_open);
  // До отгрузки долга ещё нет: принятые деньги — предоплата, о долге в текстах не говорим.
  const prepayment = order.status !== "shipped";
  const available = formatCurrency(String(maxCents / 100), order.currency);
  const amountProblem = paymentAmountError(amount, maxCents);
  const phoneOk = isKaspiInvoicePhone(phone);
  const canSubmit = !busy && !amountProblem && (flow !== "remote" || phoneOk);

  function open(next: Flow) {
    setFlow(next);
    setAmount(String(maxCents / 100));
    setMethod(methods[0]);
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
        await receivePayment(order.id, { amount, method });
        onChanged(
          prepayment
            ? `Предоплата ${sum} по заказу #${order.id} принята.`
            : `Оплата ${sum} по заказу #${order.id} принята — долг уменьшен.`,
        );
      } else {
        await api.post(`/orders/${order.id}/payments/`, {
          amount,
          method: "invoice",
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
        {remoteInvoice && (
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
            : prepayment
              ? "Деньги уже получены — это предоплата до отгрузки. Ничего клиенту не отправляется."
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

          {flow === "receive" && methods.length > 1 && (
            <div className="grid gap-2">
              <Label>Способ</Label>
              <ReceiveMethodPicker methods={methods} value={method} onChange={setMethod} />
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
