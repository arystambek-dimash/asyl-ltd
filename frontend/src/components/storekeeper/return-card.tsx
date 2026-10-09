"use client";
import { Check, ChevronRight } from "lucide-react";
import { GoodsReturnAcceptedBy, GoodsReturnStatusBadge } from "@/components/orders/goods-return-status";
import type { StorekeeperReturn } from "@/lib/types";
import { acceptedBagsLabel, bagsLabel, cn, formatDateTime } from "@/lib/utils";

/** «Возврат №13 · Основной склад · Иван Петров · 09.10.2026, 14:32» — откуда и от кого возврат. */
export function ReturnMeta({ row, className }: { row: StorekeeperReturn; className?: string }) {
  return (
    <div className={cn("text-sm font-medium tabular-nums text-[var(--muted-foreground)]", className)}>
      Возврат №{row.id} · {row.warehouse_name}
      {row.created_by_name && ` · ${row.created_by_name}`} · {formatDateTime(row.created_at)}
    </div>
  );
}

/** Сколько проверено строк: принятое кладовщиком (accepted_bags) пишет сервер. */
export function checkedCount(row: StorekeeperReturn) {
  return row.items.filter((item) => item.accepted_bags !== null).length;
}

/**
 * Мука возврата: каждая строка — мука и крупно мешки, проверенная — «✓ 15 из 16».
 * У отменённого возврата ничего не принято — только запрошенные мешки, приглушённо.
 * Один список на карточке очереди, в истории и на экране закрытого возврата.
 */
export function ReturnItemList({ row, className }: { row: StorekeeperReturn; className?: string }) {
  const cancelled = row.status === "cancelled";
  return (
    <ul aria-label="Мука" className={cn("flex flex-col gap-2", className)}>
      {row.items.map((item) => (
        <li key={item.id} className="flex items-start gap-2.5">
          <span className="min-w-0 flex-1 break-words text-base font-semibold leading-snug">{item.product_label}</span>
          {cancelled || item.accepted_bags === null ? (
            <span
              className={cn(
                "shrink-0 text-lg font-black leading-tight tabular-nums",
                cancelled ? "text-[var(--muted-foreground)]" : "text-[var(--loader-number)]",
              )}
            >
              {bagsLabel(item.bags)}
            </span>
          ) : (
            <span
              className={cn(
                "flex shrink-0 items-center gap-1 text-lg font-black leading-tight tabular-nums",
                item.accepted_bags < item.bags ? "text-[var(--warning)]" : "text-[var(--success)]",
              )}
            >
              <Check className="size-5" aria-hidden /> {item.accepted_bags} из {item.bags}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/**
 * Итог возврата: у ждущего — сколько всего и сколько строк проверено, у
 * закрытого — принято из запрошенного, у отменённого — ничего не принято.
 */
export function ReturnSummary({ row }: { row: StorekeeperReturn }) {
  if (row.status === "pending") {
    const checked = checkedCount(row);
    return (
      <>
        Итого {bagsLabel(row.bags)}
        {checked > 0 && ` · проверено ${checked} из ${row.items.length}`}
      </>
    );
  }
  if (row.status === "cancelled") return <>Ничего не принято</>;
  return (
    <>
      {row.accepted_bags === null ? `Запрошено ${bagsLabel(row.bags)}` : acceptedBagsLabel(row.accepted_bags, row.bags)}
    </>
  );
}

/**
 * Карточка возврата: клиент крупно, откуда и от кого, мука с мешками и итог.
 * В очереди открывает экран приёмки, в истории — со статусом и кто принял.
 */
export function StorekeeperReturnCard({
  row,
  onOpen,
}: {
  row: StorekeeperReturn;
  onOpen?: (row: StorekeeperReturn) => void;
}) {
  const content = (
    <>
      {/* Кладовщик сверяет возврат с человеком у склада: имя клиента первым и крупно. */}
      <div className="flex items-start justify-between gap-3">
        <span className="min-w-0 break-words text-xl font-black leading-tight">{row.client_name}</span>
        {onOpen ? (
          <ChevronRight className="mt-0.5 size-5 shrink-0 text-[var(--muted-foreground)]" />
        ) : (
          <GoodsReturnStatusBadge row={row} className="shrink-0" />
        )}
      </div>
      <ReturnMeta row={row} className="mt-1.5" />
      <ReturnItemList row={row} className="mt-3" />
      <div className="mt-3 border-t-2 border-[var(--loader-border)]/50! pt-2.5 text-sm font-bold tabular-nums">
        <ReturnSummary row={row} />
      </div>
      <GoodsReturnAcceptedBy row={row} className="mt-1 text-xs tabular-nums text-[var(--muted-foreground)]" />
    </>
  );
  const className =
    "block w-full min-w-0 overflow-hidden rounded-2xl border-2 border-[var(--loader-border)]! bg-[var(--card)] p-4 text-left shadow-card";
  if (!onOpen) return <div className={className}>{content}</div>;
  return (
    <button
      type="button"
      onClick={() => onOpen(row)}
      className={cn(className, "transition-colors active:bg-[var(--muted)]/60")}
    >
      {content}
    </button>
  );
}
