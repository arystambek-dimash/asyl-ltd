"use client";
import { useState } from "react";
import { CalendarClock, Info } from "lucide-react";
import { OptionToggle } from "@/components/orders/option-toggle";
import { PayNowFields, usePayNow } from "@/components/orders/pay-now";
import { SectionTitle } from "@/components/orders/order-form-parts";
import { ReceiveMethodPicker, receiveMethods, type ReceiveMethod } from "@/components/payments/order-payment-actions";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { can } from "@/lib/can";
import { moneyCents } from "@/lib/debt-orders";
import { todayLocalIsoDate } from "@/lib/utils";
import { useAuth } from "@/store/auth";

/** Дополнения продажи магазина: сохраняются атомарно с продажей её собственным API. */
export function useShopOrderAdditions(currency: string, totalCents: number, changed: () => void) {
  const { me } = useAuth();
  const canBackdate = can(me, "orders.edit");
  const canPay = can(me, "payments.create");
  const [backdateOn, setBackdateOn] = useState(false);
  const [date, setDate] = useState(todayLocalIsoDate);
  const [paid, setPaid] = useState(false);
  const [method, setMethod] = useState<ReceiveMethod>("cash");
  const state = usePayNow(currency, totalCents);
  const payNow: ReturnType<typeof usePayNow> = {
    ...state,
    setOn: (value) => {
      changed();
      state.setOn(value);
    },
    setMethod: (value) => {
      changed();
      state.setMethod(value);
    },
    setAmount: (value) => {
      changed();
      state.setAmount(value);
    },
  };
  const backdating = canBackdate && backdateOn;
  const payingNow = canPay && !backdating && payNow.on;
  const dateProblem = !date ? "Укажите дату." : date > todayLocalIsoDate() ? "Дата не может быть в будущем." : "";
  const methods = receiveMethods(currency);
  const paymentMethod = methods.includes(method) ? method : methods[0];
  return {
    canBackdate,
    canPay,
    backdating,
    payingNow,
    payNow,
    date,
    paid,
    methods,
    paymentMethod,
    dateProblem,
    problem: backdating ? dateProblem : payingNow ? payNow.problem : "",
    body: backdating
      ? { backdate: { date, paid: canPay && paid, payment_method: paymentMethod } }
      : payingNow
        ? { payment: { method: payNow.method, amount: (moneyCents(payNow.amount) / 100).toFixed(2) } }
        : {},
    setBackdateOn: (value: boolean) => {
      changed();
      setBackdateOn(value);
    },
    setDate: (value: string) => {
      changed();
      setDate(value);
    },
    setPaid: (value: boolean) => {
      changed();
      setPaid(value);
    },
    setMethod: (value: ReceiveMethod) => {
      changed();
      setMethod(value);
    },
    reset: () => {
      setBackdateOn(false);
      setDate(todayLocalIsoDate());
      setPaid(false);
      setMethod("cash");
      state.setOn(false);
      state.setMethod("cash");
      state.setAmount(null);
    },
  };
}

export function ShopOrderAdditions({ extras }: { extras: ReturnType<typeof useShopOrderAdditions> }) {
  if (!extras.canBackdate && !extras.canPay) return null;
  return (
    <section className="space-y-4">
      <SectionTitle step={3} title="Дополнительно" caption="Необязательно." />
      {extras.canBackdate && (
        <OptionToggle
          checked={extras.backdating}
          onChange={extras.setBackdateOn}
          icon={CalendarClock}
          tone="amber"
          title="Оформить задним числом"
          caption="Указать дату заказа и сразу зафиксировать статус и оплату."
          ariaLabel="Задним числом"
        >
          <div className="grid gap-4">
            <div className="grid gap-4 sm:grid-cols-[180px_minmax(0,1fr)]">
              <div className="grid gap-1.5">
                <Label htmlFor="shop-order-backdate-date">Дата</Label>
                <div className="relative">
                  <CalendarClock className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
                  <Input
                    id="shop-order-backdate-date"
                    type="date"
                    max={todayLocalIsoDate()}
                    value={extras.date}
                    onChange={(event) => extras.setDate(event.target.value)}
                    className="h-10 rounded-xl pl-9 tabular-nums"
                    aria-invalid={Boolean(extras.dateProblem) || undefined}
                  />
                </div>
                {extras.dateProblem && <p className="text-xs text-[var(--destructive)]">{extras.dateProblem}</p>}
              </div>
              <div className="grid content-start gap-1.5">
                <Label>Статус</Label>
                <div className="flex h-10 items-center rounded-lg border border-slate-200 bg-white px-3 text-sm font-semibold text-slate-900">
                  Завершён
                </div>
              </div>
            </div>
            {extras.canPay && (
              <div className="grid gap-2">
                <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5">
                  <input
                    type="checkbox"
                    className="size-4 accent-emerald-600"
                    checked={extras.paid}
                    onChange={(event) => extras.setPaid(event.target.checked)}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold text-slate-900">Оплачен полностью</span>
                    <span className="block text-[11px] text-slate-500">
                      Оплата на всю сумму той же датой, только в учёте магазина
                    </span>
                  </span>
                </label>
                {extras.paid && (
                  <ReceiveMethodPicker
                    methods={extras.methods}
                    value={extras.paymentMethod}
                    onChange={extras.setMethod}
                  />
                )}
              </div>
            )}
            <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2.5 text-xs text-amber-900">
              <Info className="mt-0.5 size-3.5 shrink-0 text-amber-600" />
              <span>
                Заказ запишется указанной датой. Остаток магазина при этом не списывается — он считается уже сверенным.
                Оплата учитывается отдельно от кассы мельницы.
              </span>
            </div>
          </div>
        </OptionToggle>
      )}
      {extras.canPay && !extras.backdating && (
        <>
          <PayNowFields payNow={extras.payNow} />
          {extras.payingNow && (
            <p className="text-xs text-slate-500">
              Оплата сохранится только в учёте магазина, отдельно от кассы мельницы.
            </p>
          )}
        </>
      )}
    </section>
  );
}
