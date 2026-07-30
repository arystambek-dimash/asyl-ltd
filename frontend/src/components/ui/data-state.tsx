"use client";
import { Button } from "@/components/ui/button";
import { AlertTriangle, LoaderCircle, RefreshCw } from "lucide-react";

/** Красный баннер ошибки с кнопкой «Повторить» — единый вид для всех страниц. */
export function ErrorAlert({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center gap-3 rounded-xl border border-[var(--destructive)]/20 bg-[var(--soft-red)] px-4 py-3 shadow-card"
    >
      <AlertTriangle className="size-4 shrink-0 text-[var(--destructive)]" />
      <span className="text-sm text-[var(--destructive)]">{message}</span>
      {onRetry && (
        <Button size="sm" variant="outline" className="ml-auto" onClick={onRetry}>
          <RefreshCw className="size-3.5" /> Повторить
        </Button>
      )}
    </div>
  );
}

/** Заглушка на время загрузки данных страницы и при ошибке сети.
 * Использование: if (!data) return <AppShell…><DataGate loading={loading} error={error} onRetry={reload} /></AppShell> */
export function DataGate({ loading, error, onRetry }: { loading: boolean; error?: string; onRetry?: () => void }) {
  if (loading)
    return (
      <div className="flex min-h-32 items-center justify-center rounded-2xl border border-dashed bg-[var(--card)]/60">
        <span className="flex items-center gap-2.5 text-sm font-medium text-[var(--muted-foreground)]">
          <LoaderCircle className="size-5 animate-spin text-[var(--ring)]" /> Загрузка данных
        </span>
      </div>
    );
  if (error) return <ErrorAlert message={error} onRetry={onRetry} />;
  // Загрузка завершилась без данных и без текста ошибки — например, 403 (алерт уже показан).
  return (
    <p className="rounded-2xl border border-dashed bg-[var(--card)]/60 px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
      Данные недоступны.
    </p>
  );
}
