"use client";
import { useState } from "react";
import { ChevronRight, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Numpad } from "@/components/ui/numpad";
import { remainingOf, type ClientDebtDetail } from "@/lib/debt-orders";
import type { ClientDebt, Order } from "@/lib/types";
import { formatCurrency, formatMoney } from "@/lib/utils";
import { matchesDebtQuery } from "../../debt-state";
import { posOrderBlock, wholeTengeLimit } from "./pos-logic";

const LIST =
  "divide-y divide-[var(--border)] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card";
const ROW =
  "flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-[var(--muted)]/60 disabled:cursor-not-allowed disabled:opacity-60";
const NOTE = "py-8 text-center text-sm text-[var(--muted-foreground)]";

/** Шаг 1: кого обслуживаем — поиск по должникам. */
export function PosClientStep({
  rows,
  loading,
  error,
  onRetry,
  onPick,
}: {
  rows: ClientDebt[];
  loading: boolean;
  error: string;
  onRetry: () => void;
  onPick: (id: number, name: string) => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = rows.filter((row) => matchesDebtQuery(row, query)).slice(0, 50);
  return (
    <section className="flex flex-col gap-3">
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
        <Input
          autoFocus
          aria-label="Поиск клиента"
          className="h-11 pl-9 text-base"
          placeholder="Поиск клиента: имя или телефон"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>
      {error && <ErrorAlert message={error} onRetry={onRetry} />}
      {loading && rows.length === 0 ? (
        <p className={NOTE}>Загрузка…</p>
      ) : filtered.length === 0 ? (
        !error && <p className={NOTE}>{query ? "Никого не нашли." : "Должников нет."}</p>
      ) : (
        <ul className={LIST}>
          {filtered.map((row) => (
            <li key={row.client_id}>
              <button
                type="button"
                className={ROW}
                onClick={() => onPick(row.client_id, row.client_name || `Клиент #${row.client_id}`)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[15px] font-semibold">{row.client_name || "—"}</span>
                  <span className="block text-xs text-[var(--muted-foreground)]">{row.client_phone || "—"}</span>
                </span>
                <CurrencyAmounts
                  className="shrink-0 text-sm font-semibold tabular-nums text-[var(--destructive)]"
                  byCurrency={row.debt_by_currency}
                  fallbackAmount={row.debt_total}
                  fallbackCurrency={row.debt_currency ?? "KZT"}
                />
                <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Шаг 2: какой заказ гасим; недоступные — серые, с причиной. */
export function PosOrderStep({
  clientName,
  detail,
  loading,
  error,
  onRetry,
  onPick,
}: {
  clientName: string;
  detail: ClientDebtDetail | null;
  loading: boolean;
  error: string;
  onRetry: () => void;
  onPick: (orderId: number) => void;
}) {
  const orders = detail?.orders ?? [];
  return (
    <section className="flex flex-col gap-3">
      <h2 className="px-1 text-[15px] font-semibold">{clientName}</h2>
      {error && <ErrorAlert message={error} onRetry={onRetry} />}
      {loading && !detail ? (
        <p className={NOTE}>Загрузка…</p>
      ) : orders.length === 0 ? (
        !error && <p className={NOTE}>Долгов нет.</p>
      ) : (
        <ul className={LIST}>
          {orders.map((order) => {
            const block = posOrderBlock(order, detail?.stores ?? []);
            const { max } = wholeTengeLimit(order);
            return (
              <li key={order.id}>
                <button type="button" className={ROW} disabled={block !== null} onClick={() => onPick(order.id)}>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[15px] font-semibold">
                      Заказ #{order.id}
                      {order.department_name ? ` · ${order.department_name}` : ""}
                    </span>
                    <span className="block text-xs text-[var(--muted-foreground)]">
                      {block ?? `Можно принять ${formatCurrency(max, order.currency)}`}
                    </span>
                  </span>
                  <span className="shrink-0 text-sm font-semibold tabular-nums text-[var(--destructive)]">
                    {formatCurrency(remainingOf(order), order.currency)}
                  </span>
                  {!block && <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)]" />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/** Шаг 3: сумма на клавиатуре, как в Kaspi POS. */
export function PosAmountStep({
  order,
  amount,
  error,
  busy,
  submitLabel,
  block,
  onOpenHistory,
  onDigit,
  onErase,
  onFillAll,
  onSubmit,
}: {
  order: Order;
  amount: string;
  error: string;
  busy: boolean;
  submitLabel: string;
  block: string | null;
  onOpenHistory?: () => void;
  onDigit: (digit: string) => void;
  onErase: () => void;
  onFillAll: () => void;
  onSubmit: () => void;
}) {
  if (block) {
    return (
      <section className="flex flex-col items-center gap-3 py-6 text-center">
        <div className="text-xs text-[var(--muted-foreground)]">Заказ #{order.id}</div>
        <p role="status" className="text-sm font-medium">
          {block}
        </p>
        {error && (
          <p role="alert" className="text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
        <p className="text-xs text-[var(--muted-foreground)]">Ожидающая оплата и её QR видны в «Истории».</p>
        {onOpenHistory && (
          <Button variant="outline" className="h-11 w-full" onClick={onOpenHistory}>
            Открыть «Историю»
          </Button>
        )}
      </section>
    );
  }
  const { max, tiyn } = wholeTengeLimit(order);
  const value = Number(amount || "0");
  return (
    <section className="flex flex-col gap-5">
      <div className="text-center">
        <div className="text-xs text-[var(--muted-foreground)]">
          Заказ #{order.id} · можно принять {formatCurrency(max, order.currency)}
        </div>
        <div aria-live="polite" className="mt-3 text-[44px] font-bold leading-none tracking-tight tabular-nums">
          {formatMoney(value)} ₸
        </div>
        {tiyn > 0 && (
          <div className="mt-2 text-xs text-[var(--muted-foreground)]">
            Тиыны ({formatCurrency(tiyn / 100, "KZT")}) — другим способом
          </div>
        )}
        {value !== max && (
          <Button variant="outline" size="sm" className="mt-3" onClick={onFillAll}>
            Весь остаток
          </Button>
        )}
      </div>
      <Numpad onDigit={onDigit} onBackspace={onErase} disabled={busy} />
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
      <Button className="h-12 w-full text-base" disabled={busy || value < 1} onClick={onSubmit}>
        {busy ? "Создаём…" : submitLabel}
      </Button>
    </section>
  );
}

/** «Удаленно»: телефон покупателя для счёта в Kaspi. */
export function PosPhoneStep({
  phone,
  amount,
  error,
  busy,
  onChange,
  onSubmit,
}: {
  phone: string;
  amount: number;
  error: string;
  busy: boolean;
  onChange: (phone: string) => void;
  onSubmit: () => void;
}) {
  const digits = phone.replace(/\D/g, "");
  const valid = digits.length === 10 || digits.length === 11;
  const total = formatCurrency(amount, "KZT");
  return (
    <section className="flex flex-col gap-4">
      <h2 className="px-1 text-[15px] font-semibold">Счёт на оплату в Kaspi</h2>
      <label className="flex flex-col gap-1.5">
        <span className="text-sm text-[var(--muted-foreground)]">Телефон покупателя</span>
        <Input
          type="tel"
          inputMode="tel"
          autoComplete="tel"
          className="h-11 text-base"
          placeholder="8 700 000 00 00"
          value={phone}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      <p className="text-xs text-[var(--muted-foreground)]">
        Клиенту придёт счёт в Kaspi на {total}. Долг уменьшится, когда он оплатит.
      </p>
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
      <Button className="h-12 w-full text-base" disabled={busy || !valid} onClick={onSubmit}>
        {busy ? "Отправляем…" : `Отправить счёт · ${total}`}
      </Button>
    </section>
  );
}
