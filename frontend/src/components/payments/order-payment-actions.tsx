"use client";
import { useEffect, useRef, useState } from "react";
import { Banknote, HandCoins, LoaderCircle, QrCode, Send, Smartphone } from "lucide-react";
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
import { cn, formatCurrency, formatIsoDate, todayLocalIsoDate } from "@/lib/utils";

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
export function receivePayment(
  orderId: number,
  { amount, method, date }: { amount: string; method: ReceiveMethod; date?: string | null },
) {
  return api.post(`/orders/${orderId}/payments/`, { amount, method, ...paymentDateBody(date) });
}

/**
 * День оплаты в теле запроса. `null` — кассир дату не трогал: ничего не отправляем,
 * сервер пишет «сейчас» (окно, оставленное открытым через полночь, не датирует
 * оплату вчерашним днём). Выбранный сегодняшний день тоже не отправляем.
 */
export function paymentDateBody(date?: string | null): { date?: string } {
  return date && date !== todayLocalIsoDate() ? { date } : {};
}

/**
 * «Дата оплаты» в «Принять оплату» и «Внести оплату»: пока не тронута — сегодня
 * (`value` = `null`); прошлый день — деньги получены тогда, в кассе и выписке
 * оплата встанет тем днём. Ошибка даты — прямо под полем.
 */
export function PaymentDateField({
  id,
  value,
  onChange,
  error,
}: {
  id: string;
  value: string | null;
  onChange: (value: string) => void;
  error?: string;
}) {
  const shown = value ?? todayLocalIsoDate();
  const past = Boolean(shown) && shown !== todayLocalIsoDate();
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>Дата оплаты</Label>
      <Input
        id={id}
        type="date"
        max={todayLocalIsoDate()}
        className="text-base tabular-nums"
        value={shown}
        aria-invalid={Boolean(error) || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {error ? (
        <p className="text-xs text-[var(--destructive)]">{error}</p>
      ) : (
        past && (
          <p className="text-xs text-[var(--warning)]">
            Оплата запишется {formatIsoDate(shown)} — в кассе и выписке за тот день.
          </p>
        )
      )}
    </div>
  );
}

/** Ошибка даты оплаты до запроса: будущее сервер тоже не примет; `null` — сегодня. */
export function paymentDateError(date: string | null): string {
  if (date === null) return "";
  if (!date) return "Укажите дату оплаты.";
  return date > todayLocalIsoDate() ? "Дата оплаты не может быть в будущем." : "";
}

/**
 * Кнопки способов приёма денег, по одной на способ.
 *
 * С `value` — переключатель «Оплаты сразу» в форме заказа: выбранный способ подсвечен.
 * Без `value` — шаг способа в окнах «Принять оплату» и «Внести оплату»: крупные
 * кнопки во всю ширину, заранее не выбрано ничего, нажатие сразу записывает оплату
 * этим способом. Фокус встаёт на сам список, а не на первую кнопку: лишний Enter
 * после суммы не должен записать наличные.
 */
export function ReceiveMethodPicker({
  methods,
  value,
  onChange,
  busy = null,
}: {
  methods: readonly ReceiveMethod[];
  value?: ReceiveMethod;
  onChange: (method: ReceiveMethod) => void;
  /** Способ, по которому идёт запись: на нём индикатор, все кнопки выключены. */
  busy?: ReceiveMethod | null;
}) {
  const options = RECEIVE_METHOD_OPTIONS.filter(({ key }) => methods.includes(key));
  const choosing = value === undefined;
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (choosing) list.current?.focus();
  }, [choosing]);
  return (
    <div
      ref={list}
      role={choosing ? "group" : undefined}
      aria-label={choosing ? "Способ оплаты" : undefined}
      tabIndex={choosing ? -1 : undefined}
      className={choosing ? "grid gap-2 outline-none" : "grid grid-cols-2 gap-2"}
    >
      {options.map(({ key, label, hint, icon }) => {
        const saving = busy === key;
        const Icon = saving ? LoaderCircle : icon;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={choosing ? undefined : value === key}
            aria-busy={saving || undefined}
            disabled={busy !== null}
            onClick={() => onChange(key)}
            className={cn(
              "flex items-center rounded-lg border text-left font-medium transition-colors",
              choosing
                ? cn(
                    "w-full gap-3 px-4 py-4 text-base hover:border-[var(--foreground)] hover:bg-[var(--muted)]",
                    !saving && "disabled:opacity-50",
                  )
                : cn(
                    "gap-2 px-3 py-2.5 text-sm",
                    hint && "col-span-2",
                    value === key
                      ? "border-[var(--foreground)] bg-[var(--muted)]"
                      : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--foreground)]/40",
                  ),
            )}
          >
            <Icon className={cn("shrink-0", choosing ? "size-5" : "size-4", saving && "animate-spin")} />
            <span>
              {label}
              {hint && <span className="ml-1 text-xs font-normal opacity-70">· {hint}</span>}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Заголовок окна оплаты на шаге способа — один в «Принять оплату» и «Внести оплату». */
export function methodStepHeading(sum: string) {
  return { title: `Как клиент заплатил ${sum}?`, description: "Нажмите способ — оплата запишется сразу." };
}

/**
 * Поле суммы «Принять оплату»: под полем — сколько можно принять, кнопка
 * «Весь …» подставляет эту сумму целиком, ошибка суммы — прямо под полем.
 * Им же вводят сумму во «Внести оплату» по клиенту.
 */
export function PaymentAmountField({
  id,
  value,
  onChange,
  hint,
  fullValue,
  fullLabel,
  error,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  /** Строка под полем («Остаток к оплате: …»); "" — строки нет, пока доступная сумма неизвестна. */
  hint: string;
  /** Что подставляет кнопка «Весь …»; "" — кнопки нет. */
  fullValue: string;
  fullLabel: string;
  /** Ошибка суммы; видна, только когда сумма введена. */
  error: string;
}) {
  return (
    <div className="grid gap-2">
      <Label htmlFor={id}>Сумма</Label>
      <Input
        id={id}
        type="number"
        min="0.01"
        step="0.01"
        inputMode="decimal"
        className="text-base"
        value={value}
        autoFocus
        onChange={(event) => onChange(event.target.value)}
      />
      {hint && (
        <div className="flex items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]">
          <span>{hint}</span>
          {fullValue && value !== fullValue && (
            <button
              type="button"
              className="font-medium text-[var(--foreground)] underline-offset-2 hover:underline"
              onClick={() => onChange(fullValue)}
            >
              {fullLabel}
            </button>
          )}
        </div>
      )}
      {value && error && <p className="text-xs text-[var(--destructive)]">{error}</p>}
    </div>
  );
}

/**
 * Открыть «Принять оплату» сразу — например, когда форма заказа не смогла провести
 * оплату. Способ не переносится: его выбирают после «Принять», как при обычном приёме.
 */
export interface PaymentAutoOpen {
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
  /** Открыть окно приёма сразу с этой суммой (один раз). */
  autoOpen?: PaymentAutoOpen | null;
  /** Окно открылось по `autoOpen` — страница может убрать признак из адреса. */
  onAutoOpened?: () => void;
}) {
  const [flow, setFlow] = useState<Flow | null>(null);
  const [amount, setAmount] = useState("");
  // Способ спрашиваем только после «Принять», заранее не выбран ни один:
  // с наличными по умолчанию оплату по QR записывали наличными.
  const [choosing, setChoosing] = useState(false);
  // Способ, нажатый на шаге способа: на его кнопке идёт запись.
  const [method, setMethod] = useState<ReceiveMethod | null>(null);
  const [phone, setPhone] = useState("");
  // День, которым запишутся деньги: `null` — не тронут (сегодня), прошлый — «клиент заплатил вчера».
  const [payDate, setPayDate] = useState<string | null>(null);
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
    setChoosing(false);
    setPhone("");
    setError(autoOpen.notice);
    onAutoOpened?.();
  }, [autoOpen, visible, maxCents, onAutoOpened]);
  if (!visible) return null;

  const remoteInvoice = Boolean(order.payment_request_open);
  // До отгрузки долга ещё нет: принятые деньги — предоплата, о долге в текстах не говорим.
  const prepayment = order.status !== "shipped";
  const available = formatCurrency(String(maxCents / 100), order.currency);
  const sum = formatCurrency(amount, order.currency);
  const amountProblem = paymentAmountError(amount, maxCents);
  const phoneOk = isKaspiInvoicePhone(phone);
  const dateProblem = flow === "receive" ? paymentDateError(payDate) : "";
  const canSubmit = !busy && !amountProblem && !dateProblem && (flow !== "remote" || phoneOk);
  const methodStep = flow === "receive" && choosing;

  function open(next: Flow) {
    setFlow(next);
    setAmount(String(maxCents / 100));
    setChoosing(false);
    setPhone(order.client_phone || clientPhone || "");
    setPayDate(null);
    setError("");
  }

  async function save(send: () => Promise<unknown>, notice: string) {
    setBusy(true);
    setError("");
    try {
      await send();
      onChanged(notice);
      setFlow(null);
    } catch (err) {
      // Ошибка остаётся в окне: страница под оверлеем её не покажет.
      setError(apiError(err));
    } finally {
      setBusy(false);
    }
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!canSubmit || flow === null) return;
    if (flow === "receive") {
      // Сумма готова — теперь «Как клиент заплатил?»: оплату записывает нажатый способ.
      setError("");
      setChoosing(true);
      return;
    }
    void save(
      () => api.post(`/orders/${order.id}/payments/`, { amount, method: "invoice", phone_number: phone }),
      `Счёт на ${sum} по заказу #${order.id} отправлен в Kaspi — долг уменьшится после оплаты.`,
    );
  }

  function pay(next: ReceiveMethod) {
    setMethod(next);
    const sentDate = paymentDateBody(payDate).date;
    const dated = sentDate ? ` датой ${formatIsoDate(sentDate)}` : "";
    void save(
      () => receivePayment(order.id, { amount, method: next, date: payDate }),
      prepayment
        ? `Предоплата ${sum} по заказу #${order.id} принята${dated}.`
        : `Оплата ${sum} по заказу #${order.id} принята${dated} — долг уменьшен.`,
    );
  }

  const blocked = Boolean(blockedReason);
  const heading =
    flow === "remote"
      ? {
          title: "Отправить удалённый счёт",
          description: "Счёт придёт клиенту в Kaspi на телефон. Долг уменьшится сам, когда клиент оплатит.",
        }
      : methodStep
        ? methodStepHeading(sum)
        : {
            title: "Принять оплату",
            description: prepayment
              ? "Деньги уже получены — это предоплата до отгрузки. Ничего клиенту не отправляется."
              : "Деньги уже получены — долг уменьшится сразу. Ничего клиенту не отправляется.",
          };

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
        {...heading}
        className="max-w-sm"
      >
        <form onSubmit={submit} className="flex flex-col gap-4">
          {methodStep ? (
            <ReceiveMethodPicker methods={methods} onChange={pay} busy={busy ? method : null} />
          ) : (
            <PaymentAmountField
              id={`payment-amount-${order.id}`}
              value={amount}
              onChange={setAmount}
              hint={`Остаток к оплате: ${available}`}
              fullValue={String(maxCents / 100)}
              fullLabel="Весь остаток"
              error={amountProblem}
            />
          )}

          {flow === "receive" && !methodStep && (
            <PaymentDateField
              id={`payment-date-${order.id}`}
              value={payDate}
              error={dateProblem}
              onChange={(next) => {
                // Сегодня — как не тронутая дата: окно через полночь не датирует оплату вчерашним днём.
                setPayDate(next === todayLocalIsoDate() ? null : next);
                // Отказ сервера по прежней дате к новой дате не относится.
                setError("");
              }}
            />
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
            {methodStep ? (
              <Button type="button" variant="outline" disabled={busy} onClick={() => setChoosing(false)}>
                Назад
              </Button>
            ) : (
              <>
                <Button type="button" variant="outline" disabled={busy} onClick={() => setFlow(null)}>
                  Отмена
                </Button>
                <Button type="submit" disabled={!canSubmit}>
                  {busy ? "Сохранение…" : flow === "remote" ? "Отправить счёт" : "Принять"}
                </Button>
              </>
            )}
          </div>
        </form>
      </Modal>
    </>
  );
}
