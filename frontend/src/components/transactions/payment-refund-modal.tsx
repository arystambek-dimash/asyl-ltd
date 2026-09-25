"use client";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import type { Payment, QrRefundState } from "@/lib/types";
import { formatCurrency, formatDateTime, formatPaymentNumber } from "@/lib/utils";

/**
 * Возврат подтверждённой оплаты: из кассы, через ApiPay или ссылкой по Kaspi QR
 * (`POST /payment-transactions/{id}/refund/`). Одно окно для ленты транзакций,
 * карточки заказа и «К возврату»; ошибка остаётся внутри окна. `choices` —
 * несколько оплат одного заказа: касса выбирает, какую вернуть.
 * Окно монтируется открытым — родитель рендерит его по условию.
 */
export function PaymentRefundModal({
  payment,
  choices,
  amountFor = (row) => row.available_for_refund ?? "",
  onClose,
  onRefunded,
}: {
  payment: Payment;
  choices?: readonly Payment[];
  /** Сумма по умолчанию для оплаты: вся доступная к возврату или меньше (переплата). */
  amountFor?: (payment: Payment) => string;
  onClose: () => void;
  /** Возврат оформлен; `qrRefund` — ссылка покупателю, если Kaspi ждёт его подтверждения. */
  onRefunded: (payment: Payment, qrRefund: QrRefundState | null) => unknown;
}) {
  const [selected, setSelected] = useState(payment);
  const [amount, setAmount] = useState(() => amountFor(payment));
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inFlight = useRef(false);
  const provider = selected.provider;

  function choose(id: string) {
    const next = choices?.find((row) => String(row.id) === id);
    if (!next) return;
    setSelected(next);
    setAmount(amountFor(next));
    setError("");
  }

  async function submit() {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const response = await api.post<{ method: string; qr_refund?: QrRefundState }>(
        `/payment-transactions/${selected.id}/refund/`,
        { amount: amount || undefined, reason },
      );
      await onRefunded(selected, response.data.qr_refund ?? null);
    } catch (e) {
      setError(apiError(e));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={() => !busy && onClose()}
      eyebrow={provider ? "ApiPay · Возврат" : "Касса · Возврат"}
      title="Вернуть оплату"
      description={
        provider?.channel === "qr"
          ? "Kaspi вернёт оплату по QR только после подтверждения покупателем: на экране появится QR для него, а если его нет рядом — отправьте ссылку."
          : provider
            ? "Возврат будет отправлен через ApiPay. Деньги учтутся после подтверждения платёжного сервиса."
            : "Возврат будет сразу проведён как выдача денег из кассы и уменьшит оплаченную сумму заказа."
      }
      footer={
        <>
          <Button variant="outline" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={busy || !amount || !reason.trim()} onClick={() => void submit()}>
            {busy ? "Отправка…" : provider?.channel === "qr" ? "Показать QR" : "Оформить возврат"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
        {choices && choices.length > 1 && (
          <div className="grid gap-1.5">
            <Label htmlFor={`refund-payment-${payment.id}`}>Оплата</Label>
            <Select
              id={`refund-payment-${payment.id}`}
              className="text-base"
              value={String(selected.id)}
              disabled={busy}
              onChange={(event) => choose(event.target.value)}
            >
              {choices.map((row) => (
                <option key={row.id} value={row.id}>
                  {formatPaymentNumber(row.id)} · {row.method_label} · {formatDateTime(row.paid_at)} · можно вернуть{" "}
                  {formatCurrency(row.available_for_refund ?? "0", row.currency ?? "KZT")}
                </option>
              ))}
            </Select>
          </div>
        )}
        <div className="grid gap-1.5">
          <Label htmlFor={`refund-amount-${payment.id}`}>Сумма возврата</Label>
          <Input
            id={`refund-amount-${payment.id}`}
            type="number"
            min="0.01"
            step="0.01"
            inputMode="decimal"
            className="text-base"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor={`refund-reason-${payment.id}`}>Причина</Label>
          <Input
            id={`refund-reason-${payment.id}`}
            maxLength={500}
            className="text-base"
            placeholder="Например: возврат товара"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </div>
      </div>
    </Modal>
  );
}
