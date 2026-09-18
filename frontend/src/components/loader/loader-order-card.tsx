"use client";
import { ChevronRight, TrainFront, Wallet } from "lucide-react";
import { PlateBadge } from "@/components/ui/license-plate-input";
import type { LoaderOrder } from "@/lib/loader";
import { cn, formatCurrency, formatTime, pluralRu } from "@/lib/utils";

export const bagsWord = (count: number) => pluralRu(count, ["мешок", "мешка", "мешков"]);

/** Оплата заказа одной плашкой: «Оплачен» или сколько ещё должен клиент. */
export function PaymentMark({ order, className }: { order: LoaderOrder; className?: string }) {
  if (order.payment_status === undefined) return null;
  const remaining = Number(order.remaining_amount ?? 0);
  const paid = order.payment_status === "settled" || remaining <= 0;
  const partial = !paid && Number(order.paid_total ?? 0) > 0;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-semibold",
        paid
          ? "bg-[var(--success)]/15 text-[var(--success)]"
          : partial
            ? "bg-[var(--warning)]/20 text-[var(--warning)]"
            : "bg-[var(--muted)] text-[var(--muted-foreground)]",
        className,
      )}
    >
      {paid ? (
        <>
          <Wallet className="size-3.5" /> Оплачен
        </>
      ) : (
        <>
          <Wallet className="size-3.5" />
          {partial ? "Частично" : "Не оплачен"} · {formatCurrency(String(remaining), order.currency)}
        </>
      )}
    </span>
  );
}

/** Что грузить: товары заказа одной строкой. */
export function itemsSummary(order: LoaderOrder): string {
  return order.items.map((item) => item.label).join(" · ") || "состав не указан";
}

/** Номер транспорта крупно: у грузовика — как на самой машине. */
export function TransportNumber({ order, size = "md" }: { order: LoaderOrder; size?: "md" | "lg" }) {
  if (order.transport_type === "train") {
    return (
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md border-2 border-neutral-800 bg-white px-2 py-1 font-bold tabular-nums text-neutral-900",
          size === "lg" ? "text-xl" : "text-sm",
        )}
      >
        <TrainFront className={size === "lg" ? "size-5" : "size-4"} />
        {order.truck_number || "без номера"}
      </span>
    );
  }
  if (!order.truck_number) {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-md border-2 border-dashed border-[var(--border)] px-2 py-1 font-semibold text-[var(--muted-foreground)]",
          size === "lg" ? "text-lg" : "text-sm",
        )}
      >
        Без номера
      </span>
    );
  }
  return <PlateBadge value={order.truck_number} size={size} />;
}

/** Карточка очереди: номер машины, сколько грузить, что и кому. */
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
          <TransportNumber order={order} />
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
        <span className="text-sm font-semibold tabular-nums text-[var(--muted-foreground)]">
          · {Number(order.total_kg)} кг
        </span>
      </div>
      <div className="mt-1.5 truncate text-sm font-medium">{itemsSummary(order)}</div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="min-w-0 truncate text-xs text-[var(--muted-foreground)]">
          №{order.id} · {order.client_name}
        </span>
        <PaymentMark order={order} />
      </div>
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
