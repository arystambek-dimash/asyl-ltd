"use client";
import Link from "next/link";
import { useState } from "react";
import { PaymentStageBadge } from "@/components/payment-chain";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { PAYMENT_METHOD_LABELS } from "@/lib/constants";
import { withBack } from "@/lib/navigation";
import type { PaymentQueueItem } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";
import { ActionError } from "../action-error";
import { DepartmentBadge } from "../department-badge";
import { ORDER_LISTS, OrderListRows, orderListPage, orderListTabs, type OrderListKey } from "../order-lists";
import { PaymentsQuickFilters } from "../payments-quick-filters";
import { canOpenQueueOrder } from "../scope";
import type { CashierModel } from "../use-cashier";
import type { CashierQueue } from "../use-cashier-queue";

const LIST_CLASS =
  "divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card";

function PaymentRow({
  p,
  q,
  canOpenOrder,
  canReceive,
}: {
  p: PaymentQueueItem;
  q: CashierQueue;
  canOpenOrder: boolean;
  canReceive: boolean;
}) {
  return (
    <li className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[17px] font-bold tabular-nums">{formatCurrency(p.amount, p.currency ?? "KZT")}</div>
          <div className="mt-0.5 text-[13px] text-[var(--muted-foreground)]">
            {canOpenOrder ? (
              <Link
                href={withBack(`/orders/${p.order}`, "/accounting?view=confirm")}
                className="underline-offset-2 hover:underline"
              >
                Заказ #{p.order}
              </Link>
            ) : (
              <span>Заказ #{p.order}</span>
            )}
            {" · "}
            {p.client_name} · {PAYMENT_METHOD_LABELS[p.method] ?? p.method_label}
            {p.store_name ? ` · ${p.store_name}` : ""}
            {p.received_by_name ? ` · принял ${p.received_by_name}` : ""}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <PaymentStageBadge status={p.status} />
          <DepartmentBadge name={p.department_name} color={p.department_color} />
        </div>
      </div>
      <div className="flex gap-2">
        {p.status !== "requested" || canReceive ? (
          <Button
            className="flex-1"
            disabled={q.busy}
            onClick={() => (p.status === "requested" ? q.receivePayment(p) : q.confirmPayment(p))}
          >
            {p.status === "requested" ? "Оплата поступила" : "Подтвердить получение"}
          </Button>
        ) : (
          <p className="flex-1 self-center text-xs text-[var(--muted-foreground)]">
            Принять может сотрудник с правом подтверждения оплат.
          </p>
        )}
        <Button variant="ghost" disabled={q.busy} onClick={() => q.rejectPayment(p)}>
          Отклонить
        </Button>
      </div>
    </li>
  );
}

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
      <ActionError message={q.error} />
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
          ) : !q.loadError && q.toReview.length === 0 ? (
            empty("Нет оплат, ожидающих подтверждения.")
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {q.toReview.map((p) => (
                <PaymentRow
                  key={p.id}
                  p={p}
                  q={q}
                  canOpenOrder={perms.canViewOrders && canOpenQueueOrder(model.scope.assigned, p.department)}
                  canReceive={perms.canPayments}
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
