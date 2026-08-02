"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Calculator, ListChecks, PackageCheck } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { currencySymbol, formatMoney } from "@/lib/utils";
import type { Order } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";

type Mode = "total" | "per_item";

export function OrderPriceCorrectionModal({
  order,
  onClose,
  onDone,
}: {
  order: Order | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const [mode, setMode] = useState<Mode>("total");
  const [totalAmount, setTotalAmount] = useState("");
  const [prices, setPrices] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!order) return;
    setMode("total");
    setTotalAmount(order.total_amount);
    setPrices(
      Object.fromEntries(
        order.items
          .filter((item) => item.id != null)
          .map((item) => [item.id as number, String(item.unit_price ?? item.price ?? "")]),
      ),
    );
    setBusy(false);
    setError("");
  }, [order]);

  const bags = useMemo(() => order?.items.reduce((sum, item) => sum + Number(item.quantity || 0), 0) ?? 0, [order]);
  const dividedPrice = bags > 0 && Number(totalAmount) > 0 ? Number(totalAmount) / bags : 0;
  const perItemTotal =
    order?.items.reduce((sum, item) => sum + Number(prices[item.id ?? -1] || 0) * Number(item.quantity || 0), 0) ?? 0;
  const symbol = currencySymbol(order?.currency ?? "KZT");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!order) return;
    setBusy(true);
    setError("");
    try {
      const body =
        mode === "total"
          ? { total_amount: totalAmount }
          : {
              prices: Object.fromEntries(
                order.items
                  .filter((item) => item.id != null)
                  .map((item) => [String(item.id), prices[item.id as number] ?? ""]),
              ),
            };
      await api.post(`/orders/${order.id}/correct-price/`, body);
      onDone();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={!!order}
      onClose={onClose}
      eyebrow={order ? `Финансы · Заказ #${order.id}` : "Финансы · Заказ"}
      title="Корректировать стоимость"
      description="Работает даже после отгрузки. Новая сумма сразу попадёт в заказы, кассу и задолженность клиента."
      className="max-w-2xl"
    >
      {order && (
        <form onSubmit={submit} className="space-y-5">
          <div className="grid grid-cols-3 gap-2">
            <div className="rounded-xl border bg-[var(--muted)]/35 p-3">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                Сейчас
              </div>
              <div className="mt-1 truncate text-sm font-bold tabular-nums">
                {formatMoney(order.total_amount)} {symbol}
              </div>
            </div>
            <div className="rounded-xl border bg-[var(--muted)]/35 p-3">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
                Мешков
              </div>
              <div className="mt-1 text-sm font-bold tabular-nums">{bags}</div>
            </div>
            <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-3">
              <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-emerald-700">Оплачено</div>
              <div className="mt-1 truncate text-sm font-bold tabular-nums text-emerald-950">
                {formatMoney(order.paid_total)} {symbol}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-1.5 rounded-xl bg-[var(--muted)] p-1.5">
            <button
              type="button"
              onClick={() => {
                setMode("total");
                setError("");
              }}
              className={`flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-sm font-semibold transition ${
                mode === "total"
                  ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                  : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              }`}
            >
              <Calculator className="size-4" /> Общая сумма
            </button>
            <button
              type="button"
              onClick={() => {
                setMode("per_item");
                setError("");
              }}
              className={`flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-sm font-semibold transition ${
                mode === "per_item"
                  ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                  : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              }`}
            >
              <ListChecks className="size-4" /> По позициям
            </button>
          </div>

          {mode === "total" ? (
            <section className="space-y-3 rounded-2xl border p-4">
              <div className="grid gap-2">
                <Label htmlFor="correct-order-total">Новая общая сумма заказа</Label>
                <div className="relative">
                  <Input
                    id="correct-order-total"
                    type="number"
                    min="0.01"
                    max="9999999999.99"
                    step="0.01"
                    inputMode="decimal"
                    autoFocus
                    value={totalAmount}
                    onChange={(event) => setTotalAmount(event.target.value)}
                    className="h-12 rounded-xl pr-12 text-lg font-bold tabular-nums"
                    required
                  />
                  <span className="pointer-events-none absolute right-4 top-1/2 -translate-y-1/2 font-bold text-[var(--muted-foreground)]">
                    {symbol}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-3 rounded-xl border border-blue-100 bg-blue-50/70 px-3 py-2.5 text-sm text-blue-950">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white">
                  <PackageCheck className="size-4" />
                </span>
                <div>
                  <div className="font-semibold">
                    {bags > 0 ? `${formatMoney(String(dividedPrice))} ${symbol} за мешок` : "В заказе нет мешков"}
                  </div>
                  <div className="text-xs text-blue-700">
                    {formatMoney(totalAmount || "0")} {symbol} ÷ {bags} мешков
                  </div>
                </div>
              </div>
            </section>
          ) : (
            <section className="space-y-3">
              {order.items.map((item, index) => (
                <div
                  key={item.id ?? index}
                  className="grid gap-3 rounded-2xl border bg-[var(--card)] p-3 sm:grid-cols-[minmax(0,1fr)_180px] sm:items-center"
                >
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold">{item.product_label || `Позиция ${index + 1}`}</div>
                    <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{item.quantity} мешков</div>
                  </div>
                  <div className="relative">
                    <Input
                      type="number"
                      min="0.01"
                      max="9999999999.99"
                      step="0.01"
                      inputMode="decimal"
                      aria-label={`Новая цена за мешок, ${item.product_label || `позиция ${index + 1}`}`}
                      value={prices[item.id ?? -1] ?? ""}
                      onChange={(event) => {
                        if (item.id == null) return;
                        setPrices((current) => ({ ...current, [item.id as number]: event.target.value }));
                      }}
                      className="rounded-xl pr-10 font-semibold tabular-nums"
                      required
                    />
                    <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs font-bold text-[var(--muted-foreground)]">
                      {symbol}
                    </span>
                  </div>
                </div>
              ))}
              <div className="flex justify-between rounded-xl bg-[var(--muted)]/60 px-3 py-2.5 text-sm">
                <span className="text-[var(--muted-foreground)]">Новый итог</span>
                <strong className="tabular-nums">
                  {formatMoney(String(perItemTotal))} {symbol}
                </strong>
              </div>
            </section>
          )}

          <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-900">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" />
            <span>
              Подтверждённые платежи останутся как есть. Система пересчитает остаток, долг и статус оплаты; действие
              сохранится в журнале.
            </span>
          </div>

          {error && (
            <p
              role="alert"
              className="rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm font-medium text-[var(--destructive)]"
            >
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 border-t pt-4">
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
              Отмена
            </Button>
            <Button type="submit" disabled={busy || bags <= 0}>
              {busy ? "Пересчитываем…" : "Сохранить корректировку"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
