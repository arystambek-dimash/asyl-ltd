"use client";
import { ChevronRight, Wallet } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { OrderTransportBadge } from "@/components/ui/transport-number";
import { WagonList } from "@/components/ui/wagon-list";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { loadWeight, type LoaderOrder } from "@/lib/loader";
import { bagsWord, cn, formatCurrency, formatTime } from "@/lib/utils";

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

/** Что грузить: товары заказа одной строкой. */
export function itemsSummary(order: LoaderOrder): string {
  return order.items.map((item) => item.label).join(" · ") || "состав не указан";
}

/** Карточка очереди: номер машины или вагона, сколько грузить (у вагона — в тоннах), что и кому. */
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
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 truncate">
          <OrderTransportBadge order={order} />
        </span>
        {order.shipped_at ? (
          <span className="shrink-0 text-sm font-semibold tabular-nums">{formatTime(order.shipped_at)}</span>
        ) : (
          onOpen && <ChevronRight className="size-5 shrink-0 text-[var(--muted-foreground)]" />
        )}
      </div>
      <div className="mt-2.5 flex flex-wrap items-baseline gap-x-2">
        <span className="text-3xl font-black leading-none tabular-nums">{order.bags}</span>
        <span className="text-sm font-medium text-[var(--muted-foreground)]">{bagsWord(order.bags)}</span>
        <span className="text-sm font-semibold tabular-nums text-[var(--muted-foreground)]">·</span>
        <span className="text-sm font-semibold tabular-nums text-[var(--muted-foreground)]">{loadWeight(order)}</span>
      </div>
      <div className="mt-1.5 truncate text-sm font-medium">{itemsSummary(order)}</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 truncate text-xs text-[var(--muted-foreground)]">
          №{order.id} · {order.client_name}
        </span>
        <PaymentMark order={order} />
      </div>
      {/* Номера вагонов отгрузки по отчёту; заголовок «12 вагонов» — в табличке сверху. */}
      <WagonList wagons={order.wagons} headline={false} className="mt-2.5" />
    </>
  );
  const className = cn(
    "block w-full min-w-0 overflow-hidden rounded-2xl border-2 bg-[var(--card)] p-4 text-left shadow-card",
    overdue ? "border-[var(--destructive)]" : "border-[var(--border)]",
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
