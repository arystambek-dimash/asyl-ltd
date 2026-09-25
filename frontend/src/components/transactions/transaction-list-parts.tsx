"use client";
import { Chip } from "@/components/ui/chip";
import { DataGate } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import type { Transactions } from "./use-transactions";

/* Общие куски ленты транзакций: десктопная таблица и мобильный список по дням
   различаются разметкой строк, а чипы, состояние загрузки и «Показать ещё» — одни. */

/** Мини-отчёт по статусам: пилюля = фильтр, цифра = сколько таких. */
export function TransactionStatusChips({
  t,
  className,
}: {
  t: Pick<Transactions, "statusItems" | "statusFilter" | "setStatusFilter">;
  className: string;
}) {
  return (
    <div className={className}>
      {t.statusItems.map((item) => (
        <Chip key={item.key} active={t.statusFilter === item.key} onClick={() => t.setStatusFilter(item.key)}>
          {item.label}
          <span className="tabular-nums">{item.count}</span>
        </Chip>
      ))}
    </div>
  );
}

/** Спиннер/ошибка — только пока нет ни одной строки: догрузка следующих страниц не прячет уже показанное. */
export function TransactionsListState({ t }: { t: Pick<Transactions, "rows" | "loading" | "loadError" | "reload"> }) {
  const { rows, loading, loadError } = t;
  if ((loading && rows.length === 0) || loadError) {
    return <DataGate loading={loading && rows.length === 0} error={loadError} onRetry={t.reload} />;
  }
  if (!loading && rows.length === 0) {
    return <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Транзакций пока нет.</p>;
  }
  return null;
}

/** «Показать ещё»: страницы копятся под кнопкой, итоги в конверте — по всей выборке. */
export function TransactionsLoadMore({
  t,
}: {
  t: Pick<Transactions, "rows" | "meta" | "loading" | "page" | "loadNextPage">;
}) {
  const { rows, meta } = t;
  return (
    <LoadMore
      shown={rows.length}
      total={meta?.count ?? rows.length}
      hasMore={(meta?.page ?? 1) < (meta?.pages ?? 1)}
      loading={t.loading && t.page > 1}
      onClick={t.loadNextPage}
    />
  );
}
