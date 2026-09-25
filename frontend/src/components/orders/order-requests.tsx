"use client";
import Link from "next/link";
import { useState } from "react";
import { OrderDepartmentBadge } from "@/components/ui/department-badge";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { withBack } from "@/lib/navigation";
import { formatEstimate, orderItemsSummary, requestEstimate } from "@/lib/orders";
import type { Department, Order } from "@/lib/types";
import { cn, formatDateTime } from "@/lib/utils";
import { OrderReviewDialogs } from "./order-review-dialogs";
import type { OrderRequests } from "./use-order-requests";

const BACK = "/orders?tab=requests";

function RequestCard({
  order,
  busy,
  onConfirm,
  onReject,
}: {
  order: Order;
  busy: boolean;
  onConfirm: () => void;
  onReject: () => void;
}) {
  // Заявку ещё не подтвердили: сумма — оценка по ценам позиций или прайсу клиента.
  const estimate = requestEstimate(order.items);
  return (
    <li className="flex flex-col gap-3 rounded-xl border bg-[var(--card)] p-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            href={withBack(`/orders/${order.id}`, BACK)}
            className="text-[15px] font-semibold underline-offset-2 hover:underline"
          >
            Заказ #{order.id}
          </Link>
          <div className="mt-0.5 truncate text-[13px] text-[var(--muted-foreground)]">
            {order.client_name} · {formatDateTime(order.created_at)}
          </div>
        </div>
        <OrderDepartmentBadge order={order} />
      </div>
      <div className="flex items-end justify-between gap-3">
        <p className="min-w-0 text-[13px] text-[var(--muted-foreground)]">
          {orderItemsSummary(order) || "Без позиций"}
        </p>
        <div className="shrink-0 text-right tabular-nums">
          <div
            className={cn(
              "text-[15px] font-bold",
              estimate.amount === null && "text-[13px] font-medium text-[var(--muted-foreground)]",
            )}
          >
            {formatEstimate(estimate.amount, order.currency, { approx: true })}
          </div>
          <div className="text-[12px] text-[var(--muted-foreground)]">{estimate.bags} меш.</div>
        </div>
      </div>
      <div className="flex gap-2">
        <Button className="flex-1" size="sm" disabled={busy} onClick={onConfirm}>
          Проверить и подтвердить
        </Button>
        {order.status === "pending" && (
          <Button size="sm" variant="outline" disabled={busy} onClick={onReject}>
            Отклонить
          </Button>
        )}
      </div>
    </li>
  );
}

/** Вкладка «Заявки» в «Заказах»: заявки клиентов ждут проверки цен и подтверждения. */
export function OrderRequestsSection({
  requests,
  departments,
}: {
  requests: OrderRequests;
  /** Справочник отделов страницы — окно подтверждения не запрашивает его заново. */
  departments?: Department[];
}) {
  const [confirming, setConfirming] = useState<Order | null>(null);
  const [rejecting, setRejecting] = useState<Order | null>(null);
  const { items, loading, error } = requests;
  // Окно показывает свежую строку списка; ушла со страницы — остаётся открытая заявка.
  const confirmingOrder = confirming && (items.find((order) => order.id === confirming.id) ?? confirming);

  return (
    <section className="flex flex-col gap-4">
      <OrderReviewDialogs
        departments={departments}
        confirming={confirmingOrder}
        rejecting={rejecting}
        busy={requests.busy}
        error={requests.actionError}
        onConfirm={requests.confirm}
        onConfirmClose={() => {
          setConfirming(null);
          requests.clearActionError();
        }}
        onRejectClose={() => setRejecting(null)}
        onRejected={() => void requests.reload()}
      />
      {error && <ErrorAlert message={error} onRetry={requests.reload} />}
      {loading && items.length === 0 ? (
        <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : !error && items.length === 0 ? (
        <div className="rounded-xl border border-dashed px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
          Нет заявок, ожидающих подтверждения.
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {items.map((order) => (
            <RequestCard
              key={order.id}
              order={order}
              busy={requests.busy}
              onConfirm={() => setConfirming(order)}
              onReject={() => setRejecting(order)}
            />
          ))}
        </ul>
      )}
      <LoadMore
        shown={items.length}
        total={requests.count}
        hasMore={requests.hasMore}
        loading={requests.loadingMore}
        onClick={requests.loadMore}
      />
    </section>
  );
}
