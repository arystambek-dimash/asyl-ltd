"use client";
import { Button } from "@/components/ui/button";
import { AlertTriangle, RefreshCw } from "lucide-react";

/** Красный баннер ошибки с кнопкой «Повторить» — единый вид для всех страниц. */
export function ErrorAlert({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border border-[var(--destructive)]/25 bg-[var(--destructive)]/10 px-4 py-3">
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
export function DataGate({ loading, error, onRetry }: {
  loading: boolean;
  error?: string;
  onRetry?: () => void;
}) {
  if (loading) return <p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p>;
  if (error) return <ErrorAlert message={error} onRetry={onRetry} />;
  // Загрузка завершилась без данных и без текста ошибки — например, 403 (алерт уже показан).
  return <p className="text-sm text-[var(--muted-foreground)]">Данные недоступны.</p>;
}
