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
import type { Order, PaymentQueueItem } from "@/lib/types";
import { formatCurrency } from "@/lib/utils";
import { ActionError } from "../action-error";
import { DepartmentBadge } from "../department-badge";
import { OrderReviewDialogs } from "../order-review-dialogs";
import type { CashierModel } from "../use-cashier";
import type { CashierQueue } from "../use-cashier-queue";

const LIST_CLASS =
  "divide-y divide-[var(--border)] rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card";

function PaymentRow({
  p,
  q,
  canViewOrders,
  canReceive,
}: {
  p: PaymentQueueItem;
  q: CashierQueue;
  canViewOrders: boolean;
  canReceive: boolean;
}) {
  return (
    <li className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[17px] font-bold tabular-nums">{formatCurrency(p.amount, p.currency ?? "KZT")}</div>
          <div className="mt-0.5 text-[13px] text-[var(--muted-foreground)]">
            {canViewOrders ? (
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

function RequestRow({
  o,
  q,
  onConfirm,
  onReject,
}: {
  o: Order;
  q: CashierQueue;
  onConfirm: () => void;
  onReject: () => void;
}) {
  return (
    <li className="flex flex-col gap-3 px-4 py-3.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            href={withBack(`/orders/${o.id}`, "/accounting?view=confirm")}
            className="text-[15px] font-semibold underline-offset-2 hover:underline"
          >
            Заказ #{o.id}
          </Link>
          <div className="mt-0.5 text-[13px] text-[var(--muted-foreground)]">
            {o.client_name} · {formatCurrency(o.total_amount, o.currency)}
          </div>
        </div>
        <DepartmentBadge
          name={o.department ? o.department_name || o.department : "Нет отдела"}
          color={o.department ? o.department_color : undefined}
        />
      </div>
      <div className="flex gap-2">
        <Button className="flex-1" disabled={q.busy} onClick={onConfirm}>
          Проверить и подтвердить
        </Button>
        {o.status === "pending" && (
          <Button variant="outline" disabled={q.busy} onClick={onReject}>
            Отклонить
          </Button>
        )}
      </div>
    </li>
  );
}

/** Очередь на телефоне: сегмент «Оплаты / Заявки», действия прямо в строках. */
export function ConfirmScreen({ model }: { model: CashierModel }) {
  const { queue: q, perms } = model;
  const [segment, setSegment] = useState<"payments" | "requests">("payments");
  const [confirming, setConfirming] = useState<Order | null>(null);
  const [rejecting, setRejecting] = useState<Order | null>(null);
  const showRequests = perms.canReviewOrders && segment === "requests";
  const activeRows = showRequests ? q.pendingOrders : q.toReview;
  const tabs: TabDef[] = [
    { key: "payments", label: "Оплаты", count: q.loading ? undefined : q.queuePage.count },
    ...(perms.canReviewOrders
      ? [{ key: "requests", label: "Заявки", count: q.loading ? undefined : q.pendingPage.count }]
      : []),
  ];
  const empty = (text: string) => (
    <p className="px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">{text}</p>
  );

  return (
    <section className="flex flex-col gap-4">
      <OrderReviewDialogs
        q={q}
        confirming={confirming}
        rejecting={rejecting}
        onConfirmClose={() => setConfirming(null)}
        onRejectClose={() => setRejecting(null)}
      />
      {perms.canReviewOrders && (
        <Tabs
          variant="segment"
          label="Очередь"
          className="flex w-full [&>button]:flex-1 [&>button]:justify-center"
          tabs={tabs}
          active={segment}
          onChange={(key) => setSegment(key as "payments" | "requests")}
        />
      )}
      <ActionError message={q.error} />
      {q.loadError && <ErrorAlert message={q.loadError} onRetry={q.reload} />}
      {(activeRows.length > 0 || !q.loadError) && (
        <div className={LIST_CLASS}>
          {q.loading && activeRows.length === 0 ? (
            empty("Загрузка…")
          ) : showRequests ? (
            !q.loadError && q.pendingOrders.length === 0 ? (
              empty("Нет заявок, ожидающих подтверждения.")
            ) : (
              <ul className="divide-y divide-[var(--border)]">
                {q.pendingOrders.map((o) => (
                  <RequestRow
                    key={o.id}
                    o={o}
                    q={q}
                    onConfirm={() => setConfirming(o)}
                    onReject={() => setRejecting(o)}
                  />
                ))}
              </ul>
            )
          ) : !q.loadError && q.toReview.length === 0 ? (
            empty("Нет оплат, ожидающих подтверждения.")
          ) : (
            <ul className="divide-y divide-[var(--border)]">
              {q.toReview.map((p) => (
                <PaymentRow key={p.id} p={p} q={q} canViewOrders={perms.canViewOrders} canReceive={perms.canPayments} />
              ))}
            </ul>
          )}
        </div>
      )}
      {showRequests ? (
        <LoadMore
          shown={q.pendingOrders.length}
          total={q.pendingPage.count}
          hasMore={q.pendingPage.hasMore}
          loading={q.pendingPage.loadingMore}
          onClick={q.pendingPage.loadMore}
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
