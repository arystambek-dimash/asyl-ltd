"use client";
import { ChevronRight } from "lucide-react";
import { BonusBadge } from "@/components/orders/bonus-badge";
import { OrderTransportBadge } from "@/components/ui/transport-number";
import { WagonList } from "@/components/ui/wagon-list";
import { loadWeight, type LoaderOrder, type LoaderOrderItem } from "@/lib/loader";
import { bagsLabel, bagsWord, cn, formatTime } from "@/lib/utils";

/** Крупно «70 мешков», под ним вес (у вагона — тонны) — главное, что грузчик держит в голове. */
export function BagsHeadline({ load }: { load: Pick<LoaderOrder, "bags" | "transport_type" | "total_kg"> }) {
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[56px] font-black leading-none tabular-nums text-[var(--loader-number)]">
          {load.bags}
        </span>
        <span className="text-xl font-semibold text-[var(--muted-foreground)]">{bagsWord(load.bags)}</span>
      </div>
      <div className="mt-2 text-2xl font-bold tabular-nums text-[var(--loader-number)]">{loadWeight(load)}</div>
    </div>
  );
}

/**
 * Что грузить: каждый товар своей строкой — мука с фасовкой (переносится, не
 * обрезается) и крупно мешки. Один список на карточке и на экране заказа.
 */
export function LoaderItemList({ items, className }: { items: LoaderOrderItem[]; className?: string }) {
  if (items.length === 0) {
    return <p className={cn("text-sm text-[var(--muted-foreground)]", className)}>состав не указан</p>;
  }
  return (
    <ul aria-label="Товары" className={cn("flex flex-col gap-2", className)}>
      {items.map((item, index) => (
        <li key={index} className="flex items-start gap-2.5">
          <span className="min-w-0 flex-1 break-words text-base font-semibold leading-snug">
            {item.label}
            {item.is_bonus && <BonusBadge className="ml-2 align-middle" />}
          </span>
          <span className="shrink-0 text-lg font-black leading-tight tabular-nums text-[var(--loader-number)]">
            {bagsLabel(item.quantity)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Карточка очереди: кому (крупно), номер заказа и транспорт, каждый товар с мешками и итог. */
export function LoaderOrderCard({
  order,
  onOpen,
  overdue = false,
}: {
  order: LoaderOrder;
  onOpen?: (order: LoaderOrder) => void;
  overdue?: boolean;
}) {
  const content = (
    <>
      {/* Грузчик ищет заказ по клиенту: имя первым и крупно, переносится целиком. */}
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 break-words text-xl font-black leading-tight">{order.client_name}</span>
        {order.shipped_at ? (
          <span className="shrink-0 pt-0.5 text-sm font-semibold tabular-nums">{formatTime(order.shipped_at)}</span>
        ) : (
          onOpen && <ChevronRight className="mt-0.5 size-5 shrink-0 text-[var(--muted-foreground)]" />
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-1.5 gap-y-1">
        <span className="text-sm font-medium tabular-nums text-[var(--muted-foreground)]">№{order.id} ·</span>
        <OrderTransportBadge order={order} />
      </div>
      <LoaderItemList items={order.items} className="mt-3" />
      <div className="mt-3 border-t-2 border-[var(--loader-border)]/50! pt-2.5 text-sm font-bold tabular-nums text-[var(--loader-number)]">
        Итого {bagsLabel(order.bags)} · {loadWeight(order)}
      </div>
      {/* Номера вагонов отгрузки по отчёту; заголовок «12 вагонов» — в табличке сверху. */}
      <WagonList wagons={order.wagons} headline={false} className="mt-2.5" />
    </>
  );
  const className = cn(
    "block w-full min-w-0 overflow-hidden rounded-2xl border-2 bg-[var(--card)] p-4 text-left shadow-card",
    overdue ? "border-[var(--destructive)]!" : "border-[var(--loader-border)]!",
  );
  if (!onOpen) return <div className={className}>{content}</div>;
  return (
    <button
      type="button"
      onClick={() => onOpen(order)}
      className={cn(className, "transition-colors active:bg-[var(--muted)]/60")}
    >
      {content}
    </button>
  );
}
