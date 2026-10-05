"use client";
import { ChevronRight, Wallet } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { OrderTransportBadge } from "@/components/ui/transport-number";
import { WagonList } from "@/components/ui/wagon-list";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { loadWeight, type LoaderOrder, type LoaderOrderItem } from "@/lib/loader";
import { bagsLabel, cn, formatCurrency, formatTime } from "@/lib/utils";

/**
 * Оплата заказа одной плашкой: статус с сервера и остаток долга. «Не оплачен»
 * здесь серый, а не красный: в долг возят почти всё, красным в очереди — просрочка.
 */
export function PaymentMark({ order, className }: { order: LoaderOrder; className?: string }) {
  const status = order.payment_status;
  return (
    <Badge
      tone={status === "unpaid" ? "muted" : (PAYMENT_STATUS_TONE[status] ?? "muted")}
      className={cn("h-auto rounded-lg py-1 font-semibold", className)}
    >
      <Wallet className="size-3.5" />
      {PAYMENT_STATUS_LABELS[status] ?? status}
      {status !== "settled" && ` · ${formatCurrency(order.remaining_amount, order.currency)}`}
    </Badge>
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
          <span className="min-w-0 flex-1 break-words text-base font-semibold leading-snug">{item.label}</span>
          <span className="shrink-0 text-lg font-black leading-tight tabular-nums text-[var(--loader-number)]">
            {bagsLabel(item.quantity)}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Карточка очереди: кому (крупно), номер заказа и транспорт, каждый товар с мешками, итог и оплата. */
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
      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-t-2 border-[var(--loader-border)]/50! pt-2.5">
        <span className="text-sm font-bold tabular-nums text-[var(--loader-number)]">
          Итого {bagsLabel(order.bags)} · {loadWeight(order)}
        </span>
        <PaymentMark order={order} />
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
