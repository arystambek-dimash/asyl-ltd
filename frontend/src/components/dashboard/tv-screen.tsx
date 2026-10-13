"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { TodayBoard } from "@/components/dashboard/today-board";
import type { DashboardMetrics } from "@/lib/use-dashboard-metrics";

/** Экран ТВ не должен гаснуть, пока висит сводка. Нет API или отказ — не ошибка. */
function useScreenWakeLock() {
  useEffect(() => {
    let lock: WakeLockSentinel | null = null;
    let disposed = false;
    const request = async () => {
      try {
        const next = (await navigator.wakeLock?.request("screen")) ?? null;
        if (disposed) void next?.release();
        else lock = next;
      } catch {
        lock = null;
      }
    };
    // Браузер снимает блокировку, когда вкладку скрыли; вернулись — просим снова.
    const onVisible = () => {
      if (document.visibilityState === "visible") void request();
    };
    void request();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", onVisible);
      void lock?.release().catch(() => undefined);
    };
  }, []);
}

/**
 * Режим ТВ: сводка поверх меню на весь экран. Через портал — внутри AppShell
 * у контента остаётся transform, и `fixed` там к окну не прибивается.
 * Esc выходит (в полноэкранном режиме первый Esc сначала закрывает его).
 */
export function TvScreen({ m, onExit }: { m: DashboardMetrics; onExit: () => void }) {
  useScreenWakeLock();
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onExit();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onExit]);

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Режим ТВ"
      className="fixed inset-0 z-[100] overflow-hidden bg-[var(--background)] p-[1.6vw] text-[var(--foreground)]"
    >
      <TodayBoard
        m={m}
        size="tv"
        actions={
          <button
            type="button"
            onClick={onExit}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[0.9vw] text-[var(--muted-foreground)] transition hover:bg-[var(--muted)] hover:text-[var(--foreground)]"
          >
            <X className="size-[1vw]" />
            Выйти
          </button>
        }
      />
    </div>,
    document.body,
  );
}
