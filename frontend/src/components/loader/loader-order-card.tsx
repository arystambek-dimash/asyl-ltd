"use client";
import { ChevronRight, TrainFront } from "lucide-react";
import { PlateBadge } from "@/components/ui/license-plate-input";
import type { LoaderOrder } from "@/lib/loader";
import { cn, formatTime, pluralRu } from "@/lib/utils";

export const bagsWord = (count: number) => pluralRu(count, ["мешок", "мешка", "мешков"]);

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
        <TransportNumber order={order} />
        {order.shipped_at ? (
          <span className="text-sm font-semibold tabular-nums">{formatTime(order.shipped_at)}</span>
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
      <div className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">
        №{order.id} · {order.client_name}
      </div>
    </>
  );
  const className = cn(
    "block w-full rounded-2xl border-2 bg-[var(--card)] p-4 text-left shadow-card",
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
