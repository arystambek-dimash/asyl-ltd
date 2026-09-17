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
import { AwaitingPaymentRow } from "../awaiting-payment-row";
import { DepartmentBadge } from "../department-badge";
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

/** «Оплаты» на телефоне: сегмент «Проверка / Ждут оплаты», быстрые фильтры и действия прямо в строках.
 * Оплаты к подтверждению — общая очередь всех отделов; «Ждут оплаты» — отдел кассы. */
export function ConfirmScreen({ model }: { model: CashierModel }) {
  const { queue: q, perms, me } = model;
  const [segment, setSegment] = useState<"payments" | "awaiting">("payments");
  const showAwaiting = segment === "awaiting";
  const activeRows = showAwaiting ? q.awaiting : q.toReview;
  const tabs: TabDef[] = [
    { key: "payments", label: "Проверка", count: q.loading ? undefined : q.queuePage.count },
    { key: "awaiting", label: "Ждут оплаты", count: q.loading ? undefined : q.awaitingPage.count },
  ];
  const empty = (text: string) => (
    <p className="px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">{text}</p>
  );

  return (
    <section className="flex flex-col gap-4">
      <Tabs
        variant="segment"
        label="Оплаты"
        className="flex w-full [&>button]:flex-1 [&>button]:justify-center"
        tabs={tabs}
        active={segment}
        onChange={(key) => setSegment(key as "payments" | "awaiting")}
      />
      <PaymentsQuickFilters model={model} />
      <ActionError message={q.error} />
      {q.loadError && <ErrorAlert message={q.loadError} onRetry={q.reload} />}
      {(activeRows.length > 0 || !q.loadError) && (
        <div className={LIST_CLASS}>
          {q.loading && activeRows.length === 0 ? (
            empty("Загрузка…")
          ) : showAwaiting ? (
            !q.loadError && q.awaiting.length === 0 ? (
              empty("Все отгруженные заказы оплачены или в долге.")
            ) : (
              <ul className="divide-y divide-[var(--border)]">
                {q.awaiting.map((order) => (
                  <AwaitingPaymentRow key={order.id} order={order} q={q} me={me} canOpenOrder={perms.canViewOrders} />
                ))}
              </ul>
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
      {showAwaiting ? (
        <LoadMore
          shown={q.awaiting.length}
          total={q.awaitingPage.count}
          hasMore={q.awaitingPage.hasMore}
          loading={q.awaitingPage.loadingMore}
          onClick={q.awaitingPage.loadMore}
        />
      ) : (
        <LoadMore
          shown={q.toReview.length}
          total={q.queuePage.count}
          hasMore={q.queuePage.hasMore}
          loading={q.queuePage.loadingMore}
          onClick={q.queuePage.loadMore}
        />
      )}
    </section>
  );
}
