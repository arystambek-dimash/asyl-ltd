"use client";
import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { groupByDay } from "@/lib/day-groups";
import type { CashierLogItem } from "@/lib/types";
import { useLocalDay } from "@/lib/use-local-day";
import { formatTime } from "@/lib/utils";
import { ActionError } from "../action-error";
import { RestorePaymentDialog } from "../restore-payment-dialog";
import type { CashierModel } from "../use-cashier";

/** Журнал действий по оплатам: лента по дням, как история операций в банке. */
export function JournalScreen({ model }: { model: CashierModel }) {
  const { journalLog: log, queue: q } = model;
  const [restoreEvent, setRestoreEvent] = useState<CashierLogItem | null>(null);
  const currentDay = useLocalDay();
  const groups = groupByDay(log.items, (event) => new Date(event.created_at), currentDay);

  return (
    <section className="flex flex-col gap-4">
      <ActionError message={q.error} />
      {log.error && <ErrorAlert message={log.error} onRetry={log.reload} />}
      {log.loading && log.items.length === 0 ? (
        <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : !log.error && log.items.length === 0 ? (
        <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Действий по оплатам пока нет.</p>
      ) : (
        groups.map((group) => (
          <section key={group.key} className="flex flex-col gap-2">
            <h2 className="px-1 text-[15px] font-semibold">{group.label}</h2>
            <ul className="divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
              {group.items.map((event) => (
                <li key={event.id} className="flex flex-col gap-2 px-4 py-3">
                  <div className="text-sm font-medium">{event.message}</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    {formatTime(event.created_at)}
                    {` · заказ #${event.order}`}
                    {event.client_name ? ` · ${event.client_name}` : ""}
                    {event.user_name ? ` · ${event.user_name}` : ""}
                  </div>
                  {(event.can_reopen || event.can_restore) && (
                    <div className="flex flex-wrap gap-2">
                      {event.can_reopen && (
                        <Button size="sm" variant="outline" disabled={q.busy} onClick={() => q.reopenPayment(event)}>
                          Вернуть на подтверждение
                        </Button>
                      )}
                      {event.can_restore && (
                        <Button size="sm" variant="outline" disabled={q.busy} onClick={() => setRestoreEvent(event)}>
                          <RefreshCw className="size-3.5" /> Восстановить
                        </Button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
      <LoadMore
        shown={log.items.length}
        total={log.count}
        hasMore={log.hasMore}
        loading={log.loadingMore}
        onClick={log.loadMore}
      />
      <RestorePaymentDialog q={q} event={restoreEvent} onClose={() => setRestoreEvent(null)} />
    </section>
  );
}
