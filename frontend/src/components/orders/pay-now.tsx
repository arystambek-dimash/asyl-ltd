"use client";

import { useMemo, useState } from "react";
import type { AxiosError } from "axios";
import { HandCoins } from "lucide-react";
import { OptionToggle } from "@/components/orders/option-toggle";
import {
  ReceiveMethodPicker,
  receiveMethods,
  receivePayment,
  type PaymentAutoOpen,
  type ReceiveMethod,
} from "@/components/payments/order-payment-actions";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { paymentAmountError } from "@/lib/payment-amount";
import type { Order } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";

/** «Оплата сразу» в форме заказа: способ и сумма предоплаты. */
interface PayNow {
  method: ReceiveMethod;
  amount: string;
}

// Повтор оплаты на карточке заказа: /orders/12?pay=cash&amount=500.
// `pay_check=1` — ответ сервера об оплате не пришёл, и она могла записаться.
const PAY_PARAM = "pay";
const AMOUNT_PARAM = "amount";
const CHECK_PARAM = "pay_check";

export function payRetryHref(orderId: number, payment: PayNow, { check = false }: { check?: boolean } = {}): string {
  const params = new URLSearchParams({ [PAY_PARAM]: payment.method, [AMOUNT_PARAM]: payment.amount });
  if (check) params.set(CHECK_PARAM, "1");
  return `/orders/${orderId}?${params}`;
}

/** Повтор «Оплаты сразу» на карточке заказа. */
interface PayRetry extends PaymentAutoOpen {
  /** Ответа об оплате нет — окно само не открывается, карточка просит проверить оплаченное. */
  check: boolean;
}

/** Повтор из адреса карточки; null — повтора нет. */
export function payRetryFromParams(params: URLSearchParams): PayRetry | null {
  const method = params.get(PAY_PARAM);
  if (!method) return null;
  return {
    method,
    amount: params.get(AMOUNT_PARAM) ?? "",
    notice: "Заказ создан, но оплата не прошла — проверьте сумму и примите её ещё раз.",
    check: params.get(CHECK_PARAM) === "1",
  };
}

/**
 * Что сказать на карточке вместо окна «Принять оплату»; "" — окно откроется
 * само. Если ответ об оплате потерялся, повторный приём той же суммы мог бы
 * задвоить деньги — поэтому показываем, сколько уже оплачено, и просим проверить.
 */
export function payRetryPageNotice(retry: PayRetry, order: Order): string {
  if (!order.payment_open) return "Заказ создан, но ещё не подтверждён — оплату примите после подтверждения.";
  if (retry.check)
    return `Заказ создан, но ответ об оплате не пришёл. Оплачено: ${formatCurrency(order.paid_total, order.currency)} — проверьте, прежде чем принимать деньги ещё раз.`;
  return "";
}

/** Адрес без признака повтора: окно открыто один раз, обновление страницы его не повторит. */
export function withoutPayRetry(params: URLSearchParams): string {
  const rest = new URLSearchParams(params);
  rest.delete(PAY_PARAM);
  rest.delete(AMOUNT_PARAM);
  rest.delete(CHECK_PARAM);
  const query = rest.toString();
  return query ? `?${query}` : "";
}

/**
 * Куда перейти после создания заказа с «Оплатой сразу». Подтверждённый заказ
 * (сервер открыл способ в `payment_open_methods`) сразу получает предоплату тем
 * же запросом, что и «Принять оплату». Если заказ не подтвердился или сервер
 * отказал в оплате — карточка заказа с признаком повтора: там откроется окно
 * оплаты или объяснение. Без ответа сервера (сеть, 5xx) оплата могла записаться —
 * карточка не откроет окно, а попросит проверить. Заказ уже создан, поэтому
 * форма в любом случае уходит на него.
 */
export async function payAfterCreate(order: Order, payment: PayNow): Promise<string> {
  if (!order.payment_open_methods?.includes(payment.method)) return payRetryHref(order.id, payment);
  try {
    await receivePayment(order.id, payment);
    return `/orders/${order.id}`;
  } catch (error) {
    // Отказ сервера (4xx) — деньги точно не записаны; причину покажет окно при повторе.
    const status = (error as AxiosError).response?.status;
    return payRetryHref(order.id, payment, { check: !status || status >= 500 });
  }
}

/**
 * «Оплата сразу» в черновике заказа; amount null — сумма следует за итогом.
 * `on` черновик хранит, но восстанавливает выключенным (loadOrderDraft).
 */
export interface PayNowDraft {
  on: boolean;
  method: ReceiveMethod;
  amount: string | null;
}

/**
 * Состояние «Оплаты сразу»: сумма следует за итогом заказа, пока её не правили.
 * `initial` — восстановление из черновика заказа (оттуда она приходит выключенной).
 */
export function usePayNow(currency: string, totalCents: number, initial?: PayNowDraft) {
  const [on, setOn] = useState(initial?.on ?? false);
  const [chosen, setMethod] = useState<ReceiveMethod>(initial?.method ?? "cash");
  const [typed, setAmount] = useState<string | null>(initial?.amount ?? null);
  const methods = receiveMethods(currency);
  const method = methods.includes(chosen) ? chosen : methods[0];
  const amount = typed ?? String(totalCents / 100);
  const draft = useMemo<PayNowDraft>(() => ({ on, method, amount: typed }), [on, method, typed]);
  return {
    on,
    setOn,
    methods,
    method,
    setMethod,
    amount,
    /** null — снова «весь итог». */
    setAmount,
    followsTotal: typed === null,
    totalCents,
    currency,
    problem: paymentAmountError(amount, totalCents),
    payment: { method, amount } satisfies PayNow,
    draft,
  };
}

type PayNowState = ReturnType<typeof usePayNow>;

export function PayNowFields({ payNow }: { payNow: PayNowState }) {
  return (
    <OptionToggle
      checked={payNow.on}
      onChange={payNow.setOn}
      icon={HandCoins}
      tone="emerald"
      title="Оплата сразу"
      caption="Клиент платит сейчас — предоплата примется вместе с заказом."
      ariaLabel="Оплата сразу"
    >
      <div className="grid gap-3">
        {payNow.methods.length > 1 && (
          <div className="grid gap-1.5">
            <Label>Способ</Label>
            <ReceiveMethodPicker methods={payNow.methods} value={payNow.method} onChange={payNow.setMethod} />
          </div>
        )}
        <div className="grid gap-1.5">
          <Label htmlFor="order-pay-now-amount">Сумма оплаты</Label>
          <Input
            id="order-pay-now-amount"
            type="number"
            min="0.01"
            step="0.01"
            inputMode="decimal"
            className="h-10 rounded-lg tabular-nums"
            value={payNow.amount}
            onChange={(event) => payNow.setAmount(event.target.value)}
          />
          <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
            <span>Итог заказа: {formatCurrency(String(payNow.totalCents / 100), payNow.currency)}</span>
            {!payNow.followsTotal && (
              <button
                type="button"
                className="font-medium text-slate-900 underline-offset-2 hover:underline"
                onClick={() => payNow.setAmount(null)}
              >
                Весь итог
              </button>
            )}
          </div>
          {payNow.amount && payNow.problem && <p className="text-xs text-[var(--destructive)]">{payNow.problem}</p>}
        </div>
      </div>
    </OptionToggle>
  );
}
