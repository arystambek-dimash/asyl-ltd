"use client";

/** Локальный error boundary дашборда: вместо белого экрана — кнопка повтора. */
export default function DashboardError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 px-4 text-center">
      <div className="text-sm font-medium">Не удалось отобразить дашборд</div>
      <div className="max-w-sm text-xs text-[var(--muted-foreground)]">
        Произошла ошибка на странице. Попробуйте обновить — если повторится, сообщите администратору.
      </div>
      <button
        onClick={reset}
        className="mt-1 rounded-md border bg-[var(--card)] px-3 py-1.5 text-sm font-medium shadow-sm transition-colors hover:bg-[var(--accent)]"
      >
        Обновить
      </button>
    </div>
  );
}
