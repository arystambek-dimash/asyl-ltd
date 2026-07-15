"use client";
import { useEffect, useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import { dismissToast, subscribeToasts, type Toast } from "@/lib/toast";

/** Всплывающие алерты (ошибки прав и т.п.). Монтируется один раз в корневом layout. */
export function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  useEffect(() => subscribeToasts(setToasts), []);
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-4 z-[200] flex flex-col items-center gap-2 px-4 sm:items-end sm:pr-6">
      {toasts.map((t) => (
        <div
          key={t.id}
          role="alert"
          className="animate-fade-up pointer-events-auto flex w-full max-w-sm items-start gap-2.5 rounded-md border border-[var(--destructive)]/25 bg-[var(--card)] px-3.5 py-2.5 shadow-lg"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-[var(--destructive)]" />
          <span className="text-sm text-[var(--card-foreground)]">{t.message}</span>
          <button
            aria-label="Закрыть"
            onClick={() => dismissToast(t.id)}
            className="ml-auto shrink-0 rounded p-0.5 text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)]"
          >
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
