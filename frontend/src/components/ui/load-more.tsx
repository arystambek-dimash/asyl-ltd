"use client";
import { Button } from "@/components/ui/button";

/** Кнопка ленивой подгрузки: видна, пока показаны не все строки. */
export function LoadMore({
  shown,
  total,
  hasMore,
  loading = false,
  onClick,
}: {
  shown: number;
  total: number;
  hasMore: boolean;
  loading?: boolean;
  onClick: () => void;
}) {
  if (!hasMore) return null;
  return (
    <div className="flex items-center justify-center gap-3 py-3">
      <Button type="button" variant="outline" size="sm" disabled={loading} onClick={onClick}>
        {loading ? "Загружаем…" : "Показать ещё"}
      </Button>
      <span className="text-xs tabular-nums text-[var(--muted-foreground)]">
        {shown} из {total}
      </span>
    </div>
  );
}
