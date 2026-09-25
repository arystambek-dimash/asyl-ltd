"use client";
import { useState } from "react";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { ORDER_LISTS, OrderListRows, orderListPage, orderListTabs, type OrderListKey } from "../order-lists";
import { PaymentQueueRow } from "../payment-queue-row";
import { PaymentsQuickFilters } from "../payments-quick-filters";
import { canOpenQueueOrder } from "../scope";
import type { CashierModel } from "../use-cashier";

const LIST_CLASS =
  "divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card";

/** Заказы отдела на телефоне: «К возврату» — в кассе на компьютере. */
const MOBILE_ORDER_LISTS: OrderListKey[] = ["awaiting", "shipment"];
type Segment = "payments" | OrderListKey;

/** «Оплаты» на телефоне: сегмент «Проверка / Ждут оплаты / К отгрузке», быстрые фильтры и действия
 * прямо в строках. Оплаты к подтверждению — общая очередь всех отделов; заказы — отдел кассы. */
export function ConfirmScreen({ model }: { model: CashierModel }) {
  const { queue: q, perms, me } = model;
  const [segment, setSegment] = useState<Segment>("payments");
  const list = segment === "payments" ? null : segment;
  const page = list ? orderListPage(q, list) : q.queuePage;
  const tabs: TabDef[] = [
    { key: "payments", label: "Проверка", count: q.loading ? undefined : q.queuePage.count },
    ...orderListTabs(q, MOBILE_ORDER_LISTS),
  ];
  const empty = (text: string) => (
    <p className="px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">{text}</p>
  );

  return (
    <section className="flex flex-col gap-4">
      <Tabs
        variant="segment"
        label="Оплаты"
        // Три сегмента со счётчиками должны уместиться в ширину телефона.
        className="flex w-full overflow-x-auto [&>button]:flex-1 [&>button]:justify-center [&>button]:gap-1 [&>button]:px-1.5 [&>button]:text-[13px]"
        tabs={tabs}
        active={segment}
        onChange={(key) => setSegment(key as Segment)}
      />
      <PaymentsQuickFilters model={model} />
      {q.error && <ErrorAlert message={q.error} />}
      {q.loadError && <ErrorAlert message={q.loadError} onRetry={q.reload} />}
      {(page.items.length > 0 || !q.loadError) && (
        <div className={LIST_CLASS}>
          {q.loading && page.items.length === 0 ? (
            empty("Загрузка…")
          ) : list ? (
            !q.loadError && page.items.length === 0 ? (
              empty(ORDER_LISTS[list].empty)
            ) : (
              <OrderListRows
                list={list}
                q={q}
                me={me}
                canOpenOrder={perms.canViewOrders}
                className="divide-y divide-[var(--border)]"
              />
            )
          ) : !q.loadError && q.queuePage.items.length === 0 ? (
            empty("Нет оплат, ожидающих подтверждения.")
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {q.queuePage.items.map((p) => (
                <PaymentQueueRow
                  key={p.id}
                  p={p}
                  q={q}
                  canOpenOrder={perms.canViewOrders && canOpenQueueOrder(model.scope.assigned, p.department)}
                />
              ))}
            </ul>
          )}
        </div>
      )}
      <LoadMore
        shown={page.items.length}
        total={page.count}
        hasMore={page.hasMore}
        loading={page.loadingMore}
        onClick={page.loadMore}
      />
    </section>
  );
}
