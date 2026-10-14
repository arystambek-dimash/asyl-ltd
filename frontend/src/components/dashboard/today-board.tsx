"use client";

import { useEffect, useState, type ReactNode } from "react";
import { AttentionPanel } from "@/components/dashboard/attention-panel";
import { BOARD_SIZE, type BoardSize } from "@/components/dashboard/board-size";
import { KpiTiles } from "@/components/dashboard/kpi-tiles";
import { LoadingQueue } from "@/components/dashboard/loading-queue";
import { StockPanel } from "@/components/dashboard/stock-panel";
import { ErrorAlert } from "@/components/ui/data-state";
import type { DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { cn, formatTime } from "@/lib/utils";

/** Без ответа дольше — на экране «Нет связи», чтобы застывшим числам не верили. */
const STALE_AFTER_MS = 2 * 60_000;

const DAY_FORMATTER = new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long" });

function dayTitle(isoDay: string): string {
  const [year, month, day] = isoDay.split("-").map(Number);
  const text = DAY_FORMATTER.format(new Date(year, month - 1, day));
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function useNow(intervalMs: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

/** «Обновлено 14:32» или «Нет связи с 14:30»; на ТВ ещё и крупные часы. */
function BoardStatus({ m, size }: { m: DashboardMetrics; size: BoardSize }) {
  const now = useNow(15_000);
  const s = BOARD_SIZE[size];
  const updated = m.lastUpdatedAt;
  const stale = Boolean(m.loadError) || (updated !== null && now.getTime() - updated.getTime() > STALE_AFTER_MS);

  return (
    <div className="flex items-baseline gap-3">
      {stale ? (
        <span className={cn(s.note, "font-medium text-[var(--warning)]")}>
          {updated ? `Нет связи с ${formatTime(updated)}` : "Нет связи"}
        </span>
      ) : (
        updated && <span className={cn(s.note, "text-[var(--muted-foreground)]")}>Обновлено {formatTime(updated)}</span>
      )}
      {size === "tv" && (
        <span className="text-[3vw] font-semibold leading-none tracking-tight tabular-nums">{formatTime(now)}</span>
      )}
    </div>
  );
}

function BoardSkeleton({ size }: { size: BoardSize }) {
  return (
    <div
      aria-busy="true"
      aria-label="Загрузка сводки"
      className={cn("grid flex-1 gap-3", size === "tv" && "gap-[1vw]")}
    >
      <div className="h-32 animate-pulse rounded-[18px] bg-[var(--muted)]" />
      <div className="min-h-72 animate-pulse rounded-[18px] bg-[var(--muted)]" />
    </div>
  );
}

function tvColumns(m: DashboardMetrics): string {
  if (m.canStock && m.canOrders) return "grid-cols-[1.7fr_1.2fr_0.9fr]";
  if (m.canStock || m.canOrders) return "grid-cols-[1.6fr_1fr]";
  return "grid-cols-1";
}

/** Сводка дня: одна и та же на странице и на ТВ, меняются только размеры и ссылки. */
export function TodayBoard({ m, size, actions }: { m: DashboardMetrics; size: BoardSize; actions?: ReactNode }) {
  const tv = size === "tv";
  const gap = tv ? "gap-[1vw]" : "gap-5";

  return (
    <div className={cn("flex min-w-0 flex-col", gap, tv && "h-full")}>
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h2 className={cn("font-semibold tracking-tight", tv ? "text-[2.4vw] leading-none" : "text-2xl sm:text-3xl")}>
          {dayTitle(m.today)}
        </h2>
        <div className="flex flex-wrap items-center gap-3">
          <BoardStatus m={m} size={size} />
          {actions}
        </div>
      </header>

      {m.loadError && !tv && <ErrorAlert message={m.loadError} onRetry={m.reload} />}

      {m.loading ? (
        <BoardSkeleton size={size} />
      ) : (
        <>
          <KpiTiles m={m} size={size} />
          {tv ? (
            // На ТВ высота экрана фиксирована: каждой панели — своя колонка во всю высоту.
            <div className={cn("grid min-h-0 min-w-0 flex-1", gap, tvColumns(m))}>
              {m.canStock && <StockPanel m={m} size={size} />}
              {m.canOrders && <LoadingQueue m={m} size={size} />}
              <AttentionPanel m={m} size={size} />
            </div>
          ) : (
            <div className={cn("grid min-w-0", gap, m.canStock && "xl:grid-cols-[minmax(0,1fr)_420px] xl:items-start")}>
              {m.canStock && <StockPanel m={m} size={size} />}
              <div className={cn("flex min-w-0 flex-col", gap)}>
                {m.canOrders && <LoadingQueue m={m} size={size} />}
                <AttentionPanel m={m} size={size} />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
