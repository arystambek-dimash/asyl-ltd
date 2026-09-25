"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight, PackageCheck, Truck } from "lucide-react";
import { StatusBadge } from "@/components/status-badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SearchInput } from "@/components/ui/search-input";
import { OrderTransportBadge } from "@/components/ui/transport-number";
import { MONTH_NAMES_OF, WEEKDAY_NAMES, monthGrid, monthOf, monthTitle, shiftMonth } from "@/lib/calendar-month";
import { orderedBagCount } from "@/lib/orders";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import type { Order } from "@/lib/types";
import { bagsWord, cn, formatDateTime, formatIsoDate, formatTime, pluralRu } from "@/lib/utils";

interface ShippingCalendarDay {
  day: string;
  waiting: number;
  shipped: number;
}

/** Заказы дня приходят обычной очередью поста, итоги месяца — календарём. */
interface ShippingCalendarProps {
  /** Заказы выбранного дня; null — первая загрузка. */
  orders: Order[] | null;
  /** «ГГГГ-ММ-ДД» выбранного дня. */
  day: string;
  today: string;
  search: string;
  appliedSearch: string;
  onDayChange: (day: string) => void;
  onSearchChange: (search: string) => void;
  /** orders.view — ссылка на карточку заказа. */
  canOpenOrder: boolean;
}

/** Календарь отгрузки Моноблока: итоги месяца и заказы выбранного дня. */
export function ShippingCalendar({
  orders,
  day,
  today,
  search,
  appliedSearch,
  onDayChange,
  onSearchChange,
  canOpenOrder,
}: ShippingCalendarProps) {
  const selected = day || today;
  const [month, setMonth] = useState(() => monthOf(selected));
  const calendar = useApi<{ month: string; days: ShippingCalendarDay[] }>(`/orders/shipping-calendar/?month=${month}`);
  useVisiblePolling(calendar.reload, 60_000);

  const byDay = useMemo(() => {
    const rows = new Map<string, ShippingCalendarDay>();
    for (const row of calendar.data?.days ?? []) rows.set(row.day, row);
    return rows;
  }, [calendar.data]);
  const grid = useMemo(() => monthGrid(month), [month]);
  const monthTotals = useMemo(
    () =>
      (calendar.data?.days ?? []).reduce(
        (sum, row) => ({ waiting: sum.waiting + row.waiting, shipped: sum.shipped + row.shipped }),
        { waiting: 0, shipped: 0 },
      ),
    [calendar.data],
  );

  const searching = Boolean(appliedSearch);
  const list = orders ?? [];

  function pick(iso: string) {
    onDayChange(iso === today ? "" : iso);
    if (monthOf(iso) !== month) setMonth(monthOf(iso));
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          wrapperClassName="min-w-56 flex-1"
          aria-label="Поиск"
          placeholder="Номер машины, клиент или № заказа"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
        />
        {/* Быстрый переход к дате: поиск временно отменяет правило дня. */}
        <Input
          type="date"
          aria-label="День"
          className="h-10 w-auto"
          value={selected}
          disabled={searching}
          onChange={(event) => event.target.value && pick(event.target.value)}
        />
      </div>

      {!searching && (
        <div className="rounded-xl border bg-[var(--card)] p-3 sm:p-4">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-1">
              <Button
                size="sm"
                variant="ghost"
                aria-label="Предыдущий месяц"
                onClick={() => setMonth(shiftMonth(month, -1))}
              >
                <ChevronLeft className="size-4" />
              </Button>
              <span className="min-w-40 text-center text-sm font-semibold">{monthTitle(month)}</span>
              <Button
                size="sm"
                variant="ghost"
                aria-label="Следующий месяц"
                onClick={() => setMonth(shiftMonth(month, 1))}
              >
                <ChevronRight className="size-4" />
              </Button>
            </div>
            <div className="flex items-center gap-3 text-xs text-[var(--muted-foreground)]">
              <span className="tabular-nums">
                <b className="text-[var(--foreground)]">{monthTotals.waiting}</b> к отгрузке
              </span>
              <span className="tabular-nums">
                <b className="text-[var(--success)]">{monthTotals.shipped}</b> отгружено
              </span>
              <Button size="sm" variant="outline" disabled={selected === today} onClick={() => pick(today)}>
                Сегодня
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-7 gap-1 text-center text-[11px] font-medium uppercase text-[var(--muted-foreground)]">
            {WEEKDAY_NAMES.map((name) => (
              <span key={name}>{name}</span>
            ))}
          </div>
          <div className="mt-1 grid grid-cols-7 gap-1">
            {grid.map(({ iso, day: number, outside }) => {
              const row = byDay.get(iso);
              const active = iso === selected;
              return (
                <button
                  key={iso}
                  type="button"
                  aria-pressed={active}
                  aria-label={`${number} ${MONTH_NAMES_OF[Number(iso.slice(5, 7)) - 1]}${
                    row ? `: ${row.waiting} к отгрузке, ${row.shipped} отгружено` : ", заказов нет"
                  }`}
                  onClick={() => pick(iso)}
                  className={cn(
                    "flex min-h-16 flex-col items-start gap-1 rounded-lg border p-1.5 text-left transition-colors",
                    outside ? "opacity-40" : "",
                    active
                      ? "border-[var(--ring)] bg-[var(--muted)]/60 ring-1 ring-[var(--ring)]/30"
                      : "border-transparent hover:border-[var(--border)] hover:bg-[var(--muted)]/40",
                  )}
                >
                  <span
                    className={cn(
                      "text-sm font-semibold tabular-nums",
                      iso === today && "rounded-md bg-[var(--foreground)] px-1.5 text-[var(--background)]",
                    )}
                  >
                    {number}
                  </span>
                  {row && (row.waiting > 0 || row.shipped > 0) && (
                    <span className="flex flex-wrap gap-1 text-[10px] font-semibold tabular-nums">
                      {row.waiting > 0 && (
                        <span className="rounded bg-[var(--warning)]/20 px-1 text-[var(--warning)]">{row.waiting}</span>
                      )}
                      {row.shipped > 0 && (
                        <span className="rounded bg-[var(--success)]/20 px-1 text-[var(--success)]">{row.shipped}</span>
                      )}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-base font-semibold">
            {searching
              ? "Результаты поиска"
              : selected === today
                ? "Очередь отгрузки"
                : `Показан день ${formatIsoDate(selected)}`}
          </h3>
          <span className="text-xs text-[var(--muted-foreground)] tabular-nums">
            {list.length} {pluralRu(list.length, ["заказ", "заказа", "заказов"])}
          </span>
        </div>
        {orders === null ? (
          <p className="rounded-xl border border-dashed px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
            Загружаем заказы…
          </p>
        ) : list.length === 0 ? (
          <p className="rounded-xl border border-dashed px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
            {searching ? "Ничего не найдено." : "На этот день заказов нет."}
          </p>
        ) : (
          <ul className="grid gap-2 lg:grid-cols-2">
            {list.map((order) => (
              <li key={order.id} className="flex flex-col gap-2 rounded-xl border bg-[var(--card)] p-3.5 shadow-card">
                <div className="flex items-start justify-between gap-2">
                  <OrderTransportBadge order={order} />
                  <StatusBadge status={order.status} dot />
                </div>
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-xl font-bold tabular-nums">{orderedBagCount(order)}</span>
                  <span className="text-sm text-[var(--muted-foreground)]">{bagsWord(orderedBagCount(order))}</span>
                  {order.status === "shipped" && order.shipped_at && (
                    <span className="ml-auto inline-flex items-center gap-1 text-xs text-[var(--success)]">
                      <PackageCheck className="size-3.5" /> {formatTime(order.shipped_at)}
                    </span>
                  )}
                  {order.status !== "shipped" && (order.bags_loaded ?? 0) > 0 && (
                    <span className="ml-auto inline-flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
                      <Truck className="size-3.5" /> погружено {order.bags_loaded}
                    </span>
                  )}
                </div>
                <div className="truncate text-sm">
                  {order.items.map((item) => item.product_label ?? `Товар #${item.product}`).join(" · ")}
                </div>
                <div className="flex items-center justify-between gap-2 text-xs text-[var(--muted-foreground)]">
                  <span className="truncate">
                    №{order.id} · {order.client_name}
                  </span>
                  {canOpenOrder && (
                    <Link href={`/orders/${order.id}`} className={buttonVariants({ size: "sm", variant: "ghost" })}>
                      Открыть
                    </Link>
                  )}
                </div>
                {order.arrival_date && order.status === "confirmed" && (
                  <div className="text-[11px] text-[var(--muted-foreground)]">
                    Ждём {formatDateTime(order.arrival_date)}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
