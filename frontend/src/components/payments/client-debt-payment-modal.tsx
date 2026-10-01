"use client";
import { useEffect, useState } from "react";
import {
  PaymentAmountField,
  ReceiveMethodPicker,
  receiveMethods,
  type ReceiveMethod,
} from "@/components/payments/order-payment-actions";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Segmented } from "@/components/ui/segmented";
import { api, apiError, apiErrorCode } from "@/lib/api";
import type { DebtPaymentPlan, DebtPaymentSkipped, DebtPaymentSlice } from "@/lib/debt-orders";
import { fullAmount } from "@/lib/payment-amount";
import { useDebounced } from "@/lib/use-debounced";
import {
  cn,
  currencySymbol,
  formatCurrency,
  formatIsoDayMonth,
  formatMoney,
  pluralRu,
  toLocalIsoDate,
} from "@/lib/utils";

/** Отказы предпросмотра про саму сумму: текст под полем, «Подтвердить» выключен. */
const AMOUNT_ERROR_CODES = new Set(["invalid_amount", "amount_exceeds_debt"]);

type ClientDebtPaymentProps = {
  clientId: number;
  /** Валюты долга клиента (`debt_by_currency`); переключатель — только когда их больше одной. */
  currencies: string[];
  open: boolean;
  onClose: () => void;
  /** Внесение проведено: текст уведомления для страницы. Окно после этого закрывается через `onClose`. */
  onPaid: (notice: string) => void;
};

/**
 * «Внести оплату» по клиенту: кассир вводит сумму, сервер гасит долговые
 * заказы от старого к новому (`POST /clients/{id}/debt-payment/`). Разбивку,
 * максимум и пропуски считает только сервер — окно показывает предпросмотр
 * и отправляет ту же сумму без `preview`. Поле суммы и способы — из «Принять оплату».
 */
export function ClientDebtPaymentModal({ open, ...props }: ClientDebtPaymentProps) {
  // Каждое открытие — с чистого листа: сумма, валюта и разбивка не переживают закрытие.
  if (!open) return null;
  return <ClientDebtPaymentDialog {...props} />;
}

function ClientDebtPaymentDialog({ clientId, currencies, onClose, onPaid }: Omit<ClientDebtPaymentProps, "open">) {
  const [currency, setCurrency] = useState(currencies[0] ?? "KZT");
  const [chosen, setMethod] = useState<ReceiveMethod>("cash");
  const [amount, setAmount] = useState("");
  // Последний ответ предпросмотра и сумма, для которой он посчитан ("" — весь долг).
  const [preview, setPreview] = useState<{ amount: string; plan: DebtPaymentPlan } | null>(null);
  // «Можно внести» и пропуски переживают отказ по сумме: кассир видит, почему максимум меньше долга.
  const [available, setAvailable] = useState("");
  const [skipped, setSkipped] = useState<DebtPaymentSkipped[]>([]);
  const [amountError, setAmountError] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [busy, setBusy] = useState(false);
  // Отказ при проведении: долг мог измениться после предпросмотра — пересчитать заново.
  const [refresh, setRefresh] = useState(0);

  const methods = receiveMethods(currency);
  const method = methods.includes(chosen) ? chosen : methods[0];
  const typed = amount.trim();
  const requested = useDebounced(typed);
  const url = `/clients/${clientId}/debt-payment/`;

  useEffect(() => {
    const controller = new AbortController();
    api
      .post<DebtPaymentPlan>(
        url,
        { amount: requested || null, method, currency, preview: true },
        { signal: controller.signal },
      )
      .then(({ data }) => {
        if (controller.signal.aborted) return;
        setPreview({ amount: requested, plan: data });
        setAvailable(data.total_available);
        setSkipped(data.skipped);
        setAmountError("");
        setPreviewError("");
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setPreview(null);
        const aboutAmount = AMOUNT_ERROR_CODES.has(apiErrorCode(cause));
        setAmountError(aboutAmount ? apiError(cause) : "");
        setPreviewError(aboutAmount ? "" : apiError(cause));
        // «Больше долга» приходит с потолком сервера (= total_available) — «Весь долг» не отстаёт.
        const max = (cause as { response?: { data?: { max_amount?: unknown } } }).response?.data?.max_amount;
        if (typeof max === "string") setAvailable(max);
      });
    return () => controller.abort();
  }, [url, requested, method, currency, refresh]);

  // Разбивка для введённой суммы; пока ответ на новую сумму не пришёл, прежняя видна бледной.
  const shown = preview && preview.amount ? preview.plan : null;
  const plan = preview && typed && preview.amount === typed ? preview.plan : null;
  const canSubmit = !busy && plan !== null && plan.slices.length > 0 && !amountError && !previewError;

  function changeAmount(next: string) {
    setAmount(next);
    setSubmitError("");
  }

  function changeCurrency(next: string) {
    setCurrency(next);
    setAmount("");
    setPreview(null);
    setAvailable("");
    setSkipped([]);
    setSubmitError("");
  }

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    setSubmitError("");
    try {
      const { data } = await api.post<DebtPaymentPlan>(url, { amount: typed, method, currency, preview: false });
      const count = data.slices.length;
      onPaid(
        `Внесено ${formatCurrency(data.amount, data.currency)} на ${count} ${pluralRu(count, ["заказ", "заказа", "заказов"])}`,
      );
      onClose();
    } catch (cause) {
      // Ошибка остаётся в окне: страница под оверлеем её не покажет. Отказ по сумме — под полем, как в предпросмотре.
      if (AMOUNT_ERROR_CODES.has(apiErrorCode(cause))) setAmountError(apiError(cause));
      else setSubmitError(apiError(cause));
      setRefresh((n) => n + 1);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={() => !busy && onClose()}
      variant="sheet"
      className="max-w-md"
      eyebrow="Долг клиента"
      title="Внести оплату"
      description="Деньги уже получены — долг гасится от старого заказа к новому. Ничего клиенту не отправляется."
      footer={
        <>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={!canSubmit} onClick={() => void submit()}>
            {busy
              ? "Сохранение…"
              : plan
                ? `Подтвердить · ${formatCurrency(plan.amount, plan.currency)}`
                : "Подтвердить"}
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        {currencies.length > 1 && (
          <div className="grid gap-2">
            <Label>Валюта</Label>
            <Segmented
              ariaLabel="Валюта"
              value={currency}
              onChange={changeCurrency}
              options={currencies.map((code) => ({ value: code, label: `${currencySymbol(code)} ${code}` }))}
            />
          </div>
        )}

        <PaymentAmountField
          id="client-debt-payment-amount"
          value={amount}
          onChange={changeAmount}
          hint={available ? `Можно внести: ${formatCurrency(available, currency)}` : ""}
          fullValue={fullAmount(available)}
          fullLabel="Весь долг"
          error={amountError}
        />

        {methods.length > 1 && (
          <div className="grid gap-2">
            <Label>Способ</Label>
            <ReceiveMethodPicker methods={methods} value={method} onChange={setMethod} />
          </div>
        )}

        {shown && shown.slices.length > 0 && (
          <section aria-label="Распределение" className={cn("grid gap-1.5", !plan && "opacity-60")}>
            <div className="text-xs text-[var(--muted-foreground)]">От старого заказа к новому</div>
            <ul className="divide-y rounded-lg border text-sm">
              {shown.slices.map((slice) => (
                <SliceRow key={slice.order_id} slice={slice} currency={shown.currency} />
              ))}
            </ul>
          </section>
        )}

        {skipped.length > 0 && (
          <section aria-label="Пропущены" className="grid gap-1.5">
            <div className="text-xs text-[var(--muted-foreground)]">Пропущены</div>
            <ul className="grid gap-1 text-sm text-[var(--muted-foreground)]">
              {skipped.map((row) => (
                <SkippedRow key={row.order_id} row={row} />
              ))}
            </ul>
          </section>
        )}

        <FormError message={submitError || previewError} />
      </form>
    </Modal>
  );
}

/** «#840 · отгружен 08.09 — 1 900 000 ₸ → закрыт» / «#843 — 300 000 из 1 209 500 ₸ → останется 909 500 ₸». */
function SliceRow({ slice, currency }: { slice: DebtPaymentSlice; currency: string }) {
  const shipped = slice.shipped_at
    ? ` · отгружен ${formatIsoDayMonth(toLocalIsoDate(new Date(slice.shipped_at)))}`
    : "";
  const paid = slice.closes
    ? `${formatCurrency(slice.amount, currency)} → закрыт`
    : `${formatMoney(slice.amount)} из ${formatCurrency(slice.remaining_before, currency)} → останется ${formatCurrency(slice.remaining_after, currency)}`;
  return (
    <li className="px-3 py-2 tabular-nums">
      <span className="font-medium">
        #{slice.order_id}
        {shipped}
      </span>{" "}
      — {paid}
    </li>
  );
}

function SkippedRow({ row }: { row: DebtPaymentSkipped }) {
  return (
    <li>
      #{row.order_id} — {row.detail}
    </li>
  );
}
