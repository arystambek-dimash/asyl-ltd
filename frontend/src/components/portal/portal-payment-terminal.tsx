"use client";
import { useEffect, useState } from "react";
import { ArrowLeft, FileText, HandCoins, Info, QrCode } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Numpad } from "@/components/ui/numpad";
import { ProgressBar } from "@/components/ui/progress-bar";
import { eraseAmount, fullAmount, paymentAmountError, pressAmountDigit } from "@/lib/portal-payment-amount";
import type { PortalOrder } from "@/lib/types";
import { cn, currencySymbol, formatMoney } from "@/lib/utils";
import { PortalPaymentParts, type PortalPaymentPart } from "./portal-payment-parts";

export type PortalPayMethod = "kaspi" | "invoice";

const TILE =
  "flex min-h-[84px] flex-col items-start justify-between gap-2 rounded-2xl border p-4 text-left transition-colors " +
  "disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50";

/**
 * Оплата заказа в кабинете, как POS-терминал: крупный остаток, сумма на клавиатуре
 * и два способа Kaspi — QR сразу или счёт на телефон. Наличных и «в долг» нет:
 * неоплаченный остаток отгруженного заказа и так учитывается долгом.
 */
export function PortalPaymentCard({
  order,
  busy,
  error = "",
  onPay,
  onRelease,
}: {
  order: PortalOrder;
  busy: boolean;
  /** Ошибка оплаты от сервера — показывается в самом терминале, а не над страницей. */
  error?: string;
  onPay: (method: PortalPayMethod, amount: string, phone?: string) => Promise<void>;
  onRelease: (part: PortalPaymentPart) => void;
}) {
  const symbol = currencySymbol(order.currency);
  const total = Number(order.total_amount ?? 0);
  const paid = Number(order.paid_total ?? 0);
  const remaining = Math.max(0, Number(order.remaining_amount ?? 0));
  const available = Math.max(0, Number(order.available_amount ?? remaining));
  const pct = total > 0 ? Math.min(100, Math.round((paid / total) * 100)) : 0;
  const availableValue = order.available_amount ?? order.remaining_amount;

  const [amount, setAmount] = useState(() => fullAmount(availableValue));
  const [invoiceOpen, setInvoiceOpen] = useState(false);
  const [phone, setPhone] = useState(order.client_phone ?? "");
  // Доступная сумма меняется после оплаты или отмены — терминал снова предлагает весь остаток.
  useEffect(() => {
    setAmount(fullAmount(availableValue));
    setInvoiceOpen(false);
  }, [availableValue, order.id]);
  useEffect(() => setPhone(order.client_phone ?? ""), [order.client_phone, order.id]);

  const amountError = paymentAmountError(amount, available);
  const full = fullAmount(availableValue);
  const canPay = order.currency === "KZT" && !busy && !amountError;

  return (
    <section aria-label="Оплата" className="overflow-hidden rounded-3xl border bg-[var(--card)] shadow-card">
      <div className="px-5 pb-4 pt-5 text-center sm:px-6">
        <div className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
          Остаток к оплате
        </div>
        <div className="mt-1 text-[40px] font-bold leading-tight tracking-tight tabular-nums">
          {formatMoney(remaining)} {symbol}
        </div>
        <div className="mx-auto mt-3 max-w-xs">
          <ProgressBar pct={pct} />
          <div className="mt-1.5 text-xs tabular-nums text-[var(--muted-foreground)]">
            Оплачено {formatMoney(paid)} из {formatMoney(total)} {symbol}
          </div>
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t bg-[var(--muted)]/20 px-4 py-4 sm:px-6">
        {order.debt_requested && (
          <div className="flex items-start gap-2 rounded-2xl border border-[var(--primary)]/25 bg-[var(--primary)]/5 p-3 text-sm">
            <HandCoins className="mt-0.5 size-4 shrink-0 text-[var(--primary)]" />
            Запрос «В долг» отправлен. Оплатить остаток можно в любой момент здесь же.
          </div>
        )}

        <PortalPaymentParts order={order} busy={busy} onRelease={onRelease} />

        {error && (
          <p
            role="alert"
            className="rounded-2xl bg-[var(--destructive)]/10 px-4 py-3 text-center text-sm text-[var(--destructive)]"
          >
            {error}
          </p>
        )}

        {order.currency !== "KZT" ? (
          <p className="flex items-start gap-2 rounded-2xl bg-[var(--card)] p-4 text-sm text-[var(--muted-foreground)]">
            <Info className="mt-0.5 size-4 shrink-0" />
            Оплата в долларах — в кассе ASYL. Остаток учитывается как долг.
          </p>
        ) : (
          available > 0 && (
            <div className="flex flex-col gap-4 rounded-2xl bg-[var(--card)] p-4 shadow-sm">
              <div className="text-center">
                <div className="text-xs text-[var(--muted-foreground)]">Сумма оплаты</div>
                <div
                  aria-live="polite"
                  aria-label="Сумма оплаты"
                  className="mt-1 text-[34px] font-bold leading-tight tracking-tight tabular-nums"
                >
                  {formatMoney(Number(amount || 0))} {symbol}
                </div>
                {amount !== full && (
                  <Button variant="outline" size="sm" className="mt-2" disabled={busy} onClick={() => setAmount(full)}>
                    Весь остаток · {formatMoney(available)} {symbol}
                  </Button>
                )}
                {amount && amountError && (
                  <p role="alert" className="mt-2 text-xs text-[var(--destructive)]">
                    {amountError}
                  </p>
                )}
              </div>

              {!invoiceOpen && (
                <Numpad
                  className="mx-auto w-full max-w-xs"
                  disabled={busy}
                  onDigit={(digit) => setAmount((current) => pressAmountDigit(current, digit))}
                  onBackspace={() => setAmount((current) => eraseAmount(current))}
                />
              )}

              {invoiceOpen ? (
                <div className="flex flex-col gap-3">
                  <label className="flex flex-col gap-1.5">
                    <span className="text-sm text-[var(--muted-foreground)]">Телефон для счёта в Kaspi</span>
                    <Input
                      type="tel"
                      inputMode="tel"
                      autoComplete="tel"
                      className="h-12 text-base"
                      placeholder="8 700 000 00 00"
                      value={phone}
                      onChange={(event) => setPhone(event.target.value)}
                    />
                  </label>
                  <Button
                    className="h-12 w-full text-base"
                    disabled={!canPay || !phone.trim()}
                    onClick={() => void onPay("invoice", amount, phone.trim())}
                  >
                    {busy ? "Отправляем…" : `Отправить счёт · ${formatMoney(Number(amount || 0))} ${symbol}`}
                  </Button>
                  <Button variant="ghost" className="w-full" disabled={busy} onClick={() => setInvoiceOpen(false)}>
                    <ArrowLeft className="size-4" /> Назад
                  </Button>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    disabled={!canPay}
                    onClick={() => void onPay("kaspi", amount)}
                    className={cn(
                      TILE,
                      "border-transparent bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-95",
                    )}
                  >
                    <QrCode className="size-6" aria-hidden />
                    <span>
                      <span className="block text-[15px] font-semibold">{busy ? "Создаём…" : "Kaspi QR"}</span>
                      <span className="block text-xs opacity-80">Оплатить сейчас</span>
                    </span>
                  </button>
                  <button
                    type="button"
                    disabled={!canPay}
                    onClick={() => setInvoiceOpen(true)}
                    className={cn(TILE, "border-[var(--border)] bg-[var(--card)] hover:bg-[var(--muted)]/60")}
                  >
                    <FileText className="size-6 text-[var(--primary)]" aria-hidden />
                    <span>
                      <span className="block text-[15px] font-semibold">Счёт в Kaspi</span>
                      <span className="block text-xs text-[var(--muted-foreground)]">Придёт на телефон</span>
                    </span>
                  </button>
                </div>
              )}
            </div>
          )
        )}

        <p className="flex items-start gap-1.5 px-1 text-xs text-[var(--muted-foreground)]">
          <Info className="mt-0.5 size-3.5 shrink-0" />
          Не оплатили сейчас — остаток остаётся долгом, оплатить можно позже здесь же. Можно платить частями.
        </p>
      </div>
    </section>
  );
}
