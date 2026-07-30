"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, X } from "lucide-react";
import { dismissToast, subscribeToasts, type Toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

/** Всплывающие алерты (ошибки прав и т.п.). Монтируется один раз в корневом layout. */
export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  useEffect(() => subscribeToasts(setToasts), []);
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(1rem+env(safe-area-inset-bottom))] z-[200] flex flex-col items-center gap-2 px-3 sm:items-end sm:pr-6">
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.kind === "success" ? "status" : "alert"}
          className={cn(
            "animate-fade-up pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-2xl border bg-[var(--card)] px-4 py-3.5 shadow-float",
            t.kind === "success" ? "border-[var(--success)]/20" : "border-[var(--destructive)]/20",
          )}
        >
          {t.kind === "success" ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[var(--success)]" />
          ) : (
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-[var(--destructive)]" />
          )}
          <span className="text-sm text-[var(--card-foreground)]">{t.message}</span>
          <button
            aria-label="Закрыть"
            onClick={() => dismissToast(t.id)}
            className="ml-auto flex size-11 shrink-0 items-center justify-center rounded-xl text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)] sm:size-9"
          >
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
