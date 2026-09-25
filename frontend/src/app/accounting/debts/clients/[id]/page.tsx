"use client";
import { Fragment, use, useMemo, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { OtherCurrencyRows } from "@/components/ui/currency-amounts";
import { buttonVariants } from "@/components/ui/button";
import { PaymentHistoryTable } from "@/components/payment-history-table";
import { Tabs } from "@/components/ui/tabs";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { useApi } from "@/lib/use-api";
import { useIsMobile } from "@/lib/use-media-query";
import { withBack } from "@/lib/navigation";
import { amountForCurrency, fieldByCurrency, primaryMoneyCurrency } from "@/lib/currency-map";
import { cn, formatCompactCurrency, formatCurrency, formatDateTime, toggledSet } from "@/lib/utils";
import { orderTransportText } from "@/lib/wagons";
import { can } from "@/lib/can";
import { PaymentStageBadge, paidByMethod, paymentNetAmount } from "@/components/payment-chain";
import { PaidMethodSummary } from "@/components/transactions/paid-method-summary";
import { OrderPaymentActions } from "@/components/payments/order-payment-actions";
import { OrderPaymentBadge } from "@/components/payments/order-payment-badge";
import { useAuth } from "@/store/auth";
import { ArrowLeft, ChevronDown, ExternalLink, Info, Phone } from "lucide-react";
import type { ClientHistory, Me, Order } from "@/lib/types";
import {
  blockingStore,
  pendingSum,
  remainingOf,
  storeBlockReason,
  type ClientDebtDetail,
  type DebtStore,
} from "@/lib/debt-orders";

/* Денежная плитка шапки: сокращённая сумма в основной валюте, точная — в подсказке, прочие валюты строками ниже. */
function MoneyStat({
  label,
  amount,
  currency,
  byCurrency,
  className,
}: {
  label: string;
  amount: string | number;
  currency: string;
  byCurrency: Record<string, string>;
  className?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-[var(--muted-foreground)]">{label}</div>
      <div
        title={formatCurrency(amount, currency)}
        className={cn("mt-1 truncate text-lg font-semibold leading-none tabular-nums", className)}
      >
        {formatCompactCurrency(amount, currency)}
      </div>
      <OtherCurrencyRows byCurrency={byCurrency} primary={currency} />
    </div>
  );
}

/* ── Счёт по заказу: зафиксированные клиентские цены ───────────────────── */
function InvoiceTable({ order, layout = "full" }: { order: Order; layout?: "full" | "compact" }) {
  const lines = order.items.map((it) => {
    const price = Number(it.unit_price ?? 0);
    return {
      key: it.id ?? `${it.product}`,
      label: it.product_label ?? `Товар #${it.product}`,
      qty: it.quantity,
      price,
      total: price * it.quantity,
    };
  });
  const total = Number(order.total_amount);
  const paid = Number(order.paid_total);
  const toPay = remainingOf(order);
  return (
    <div>
      {layout === "compact" ? (
        <ul className="divide-y">
          {lines.map((l) => (
            <li key={l.key} className="flex items-start justify-between gap-3 py-2 text-sm">
              <div>
                <div className="font-medium">{l.label}</div>
                <div className="text-xs tabular-nums text-[var(--muted-foreground)]">
                  {l.qty} × {formatCurrency(l.price, order.currency)}
                </div>
              </div>
              <div className="font-medium tabular-nums">{formatCurrency(l.total, order.currency)}</div>
            </li>
          ))}
        </ul>
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>Товар</TH>
              <TH className="text-right">Количество</TH>
              <TH className="text-right">Цена за единицу</TH>
              <TH className="text-right">Итого</TH>
            </TR>
          </THead>
          <TBody>
            {lines.map((l) => (
              <TR key={l.key}>
                <TD className="font-medium">{l.label}</TD>
                <TD className="text-right tabular-nums">{l.qty}</TD>
                <TD className="text-right tabular-nums">{formatCurrency(l.price, order.currency)}</TD>
                <TD className="text-right tabular-nums font-medium">{formatCurrency(l.total, order.currency)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <div
        className={cn(
          "mt-3 flex flex-col gap-1.5 border-t pt-3 text-sm",
          layout === "compact" ? "w-full" : "ml-auto max-w-xs",
        )}
      >
        <div className="flex justify-between text-[var(--muted-foreground)]">
          <span>Сумма заказа</span>
          <span className="tabular-nums">{formatCurrency(total, order.currency)}</span>
        </div>
        {paid > 0 && (
          <div className="flex justify-between text-[var(--success)]">
            <span>Уже оплачено</span>
            <span className="tabular-nums">−{formatCurrency(paid, order.currency)}</span>
          </div>
        )}
        <div className="flex justify-between text-base font-semibold">
          <span>Остаток к оплате</span>
          <span className="tabular-nums">{formatCurrency(toPay, order.currency)}</span>
        </div>
      </div>
    </div>
  );
}

/* ── Списания: погашения по заказу, включая цепочку подтверждения ───────── */
function WriteOffList({ order }: { order: Order }) {
  const rows = [...(order.payments ?? []), ...(order.pending_payments ?? [])];
  if (rows.length === 0) {
    return <p className="py-4 text-sm text-[var(--muted-foreground)]">Списаний пока нет.</p>;
  }
  return (
    <div className="flex flex-col divide-y">
      {rows.map((p) => {
        // Как в «Уже оплачено» и истории клиента: чистая сумма (без возврата)
        // и день признания оплаты — подтверждение, а не приём.
        const currency = p.currency ?? order.currency;
        const refunded = Number(p.refunded_amount ?? 0);
        return (
          <div key={p.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="tabular-nums">{formatDateTime(p.confirmed_at ?? p.paid_at)}</span>
                <span className="text-[var(--muted-foreground)]">{p.method_label}</span>
                <PaymentStageBadge payment={p} />
              </div>
              {(p.recorded_by_name || p.note) && (
                <div className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">
                  {[p.recorded_by_name, p.note].filter(Boolean).join(" · ")}
                </div>
              )}
            </div>
            <div className="shrink-0 text-right">
              <span
                className={cn(
                  "tabular-nums font-semibold",
                  p.status === "confirmed" ? "text-[var(--success)]" : "text-[var(--muted-foreground)]",
                )}
              >
                +{formatCurrency(paymentNetAmount(p), currency)}
              </span>
              {refunded > 0 && (
                <div className="text-xs tabular-nums text-[var(--muted-foreground)]">
                  из {formatCurrency(p.amount, currency)}, возврат {formatCurrency(refunded, currency)}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* Детали раскрытой строки: резерв, позиции счёта и списание. */
function DebtOrderDetails({ order, layout = "full" }: { order: Order; layout?: "full" | "compact" }) {
  const [tab, setTab] = useState("invoice");
  const pending = pendingSum(order);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--muted-foreground)]">
        {order.department && <span>{order.department_name}</span>}
        {orderTransportText(order) && <span className="tabular-nums">{orderTransportText(order)}</span>}
        <span>
          Сумма заказа: <b className="tabular-nums">{formatCurrency(order.total_amount, order.currency)}</b>
        </span>
        <PaidMethodSummary parts={paidByMethod(order)} className="text-xs" />
      </div>
      {pending > 0 && (
        <div className="flex items-center justify-between rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-sm">
          <span className="flex items-center gap-1.5 text-[var(--warning)]">
            <Info className="size-4" />
            Уже зарезервировано и ожидает оплаты либо подтверждения
          </span>
          <span className="tabular-nums font-semibold text-[var(--warning)]">
            {formatCurrency(pending, order.currency)}
          </span>
        </div>
      )}
      <Tabs
        tabs={[
          { key: "invoice", label: "Счёт" },
          { key: "writeoff", label: "Списание" },
        ]}
        active={tab}
        onChange={setTab}
      />
      <div>{tab === "invoice" ? <InvoiceTable order={order} layout={layout} /> : <WriteOffList order={order} />}</div>
    </div>
  );
}

/* Заказы в долге: строка (на телефоне — карточка) раскрывается в детали,
 * а в деталях — единственные действия по деньгам заказа. Отдельных кнопок
 * «Оплатить»/«Открыть» в свёрнутой строке нет. */
type DebtOrderContext = {
  me: Me | null;
  canViewOrder: boolean;
  clientId: string;
  clientPhone?: string | null;
  blockedFor: (order: Order) => DebtStore | null;
  onPaid: (notice: string) => void;
};

function DebtOrderActions({ order, ctx }: { order: Order; ctx: DebtOrderContext }) {
  const store = ctx.blockedFor(order);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <OrderPaymentActions
        order={order}
        me={ctx.me}
        clientPhone={ctx.clientPhone}
        blockedReason={store ? storeBlockReason(store) : null}
        onChanged={ctx.onPaid}
      />
      {ctx.canViewOrder && (
        <Link
          href={withBack(`/orders/${order.id}`, `/accounting/debts/clients/${ctx.clientId}`)}
          className={cn(buttonVariants({ size: "sm", variant: "ghost" }), "ml-auto")}
        >
          Карточка заказа <ExternalLink className="size-3.5" />
        </Link>
      )}
    </div>
  );
}

function DetailsToggleLabel({ open }: { open: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 text-sm font-medium text-[var(--primary)]">
      {open ? "Скрыть детали" : "Открыть детали"}
      <ChevronDown className={cn("size-4 transition-transform", open ? "rotate-180" : "rotate-0")} />
    </span>
  );
}

function DebtOrdersTable({
  orders,
  layout,
  ctx,
}: {
  orders: Order[];
  layout: "table" | "cards";
  ctx: DebtOrderContext;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // Единый стиль ленивых списков: длинный долг не разворачивается простынёй.
  const [limit, setLimit] = useState(25);
  const visible = orders.slice(0, limit);

  function toggle(id: number) {
    setExpanded((current) => toggledSet(current, id));
  }

  const loadMore = (
    <LoadMore
      shown={visible.length}
      total={orders.length}
      hasMore={orders.length > visible.length}
      onClick={() => setLimit((current) => current + 25)}
    />
  );

  if (layout === "cards") {
    return (
      <div className="flex flex-col gap-3">
        <ul className="flex flex-col gap-3">
          {visible.map((order) => {
            const open = expanded.has(order.id);
            const detailsId = `debt-order-${order.id}-details`;
            return (
              <li
                key={order.id}
                className="flex flex-col rounded-xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-card"
              >
                <button
                  type="button"
                  aria-expanded={open}
                  aria-controls={detailsId}
                  onClick={() => toggle(order.id)}
                  className="flex w-full flex-col gap-2.5 text-left"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="text-[15px] font-semibold">#{order.id}</span>
                      <span className="text-xs text-[var(--muted-foreground)]">{order.department_name}</span>
                    </div>
                    <OrderPaymentBadge order={order} dot />
                  </div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    Создан {formatDateTime(order.created_at)} · Отгружен{" "}
                    {order.shipped_at ? formatDateTime(order.shipped_at) : "—"}
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <div className="text-[11px] text-[var(--muted-foreground)]">Оплачено</div>
                      <div className="tabular-nums text-[var(--success)]">
                        {formatCurrency(order.paid_total, order.currency)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-[var(--muted-foreground)]">Остаток</div>
                      <div className="font-semibold tabular-nums text-[var(--destructive)]">
                        {formatCurrency(remainingOf(order), order.currency)}
                      </div>
                    </div>
                  </div>
                  <div className="border-t pt-2.5">
                    <DetailsToggleLabel open={open} />
                  </div>
                </button>
                {open && (
                  <div id={detailsId} className="mt-3 flex flex-col gap-3 border-t pt-3">
                    <DebtOrderActions order={order} ctx={ctx} />
                    <DebtOrderDetails order={order} layout="compact" />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {loadMore}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card">
      <Table>
        <THead>
          <TR>
            <TH>Заказ</TH>
            <TH>Создан</TH>
            <TH>Отгружен</TH>
            <TH>Статус</TH>
            <TH className="text-right">Оплачено</TH>
            <TH className="text-right">Остаток</TH>
            <TH />
          </TR>
        </THead>
        <TBody>
          {visible.map((order) => {
            const open = expanded.has(order.id);
            const detailsId = `debt-order-${order.id}-details`;
            return (
              <Fragment key={order.id}>
                <TR
                  className={cn(
                    "cursor-pointer transition-colors hover:bg-[var(--muted)]/40",
                    open && "bg-[var(--muted)]/30",
                  )}
                  onClick={() => toggle(order.id)}
                >
                  <TD className="font-medium">#{order.id}</TD>
                  <TD className="tabular-nums">{formatDateTime(order.created_at)}</TD>
                  <TD className="tabular-nums">{order.shipped_at ? formatDateTime(order.shipped_at) : "—"}</TD>
                  <TD>
                    <OrderPaymentBadge order={order} dot />
                  </TD>
                  <TD className="text-right tabular-nums text-[var(--success)]">
                    {formatCurrency(order.paid_total, order.currency)}
                  </TD>
                  <TD className="text-right tabular-nums font-semibold text-[var(--destructive)]">
                    {formatCurrency(remainingOf(order), order.currency)}
                  </TD>
                  <TD className="text-right">
                    <button
                      type="button"
                      aria-expanded={open}
                      aria-controls={detailsId}
                      aria-label={`${open ? "Скрыть" : "Открыть"} детали заказа #${order.id}`}
                      onClick={(event) => {
                        event.stopPropagation();
                        toggle(order.id);
                      }}
                      className="whitespace-nowrap rounded-md px-2 py-1 hover:bg-[var(--muted)]"
                    >
                      <DetailsToggleLabel open={open} />
                    </button>
                  </TD>
                </TR>
                {open && (
                  <TR id={detailsId} className="bg-[var(--muted)]/30">
                    <TD colSpan={7} className="p-4">
                      <div className="flex flex-col gap-3">
                        <DebtOrderActions order={order} ctx={ctx} />
                        <DebtOrderDetails order={order} />
                      </div>
                    </TD>
                  </TR>
                )}
              </Fragment>
            );
          })}
        </TBody>
      </Table>
      {loadMore}
    </div>
  );
}

/* ── Страница ───────────────────────────────────────────────────────────── */
function ClientDebtPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const mobile = useIsMobile();
  const canViewReports = can(me, "reports.view");
  const canViewOrders = can(me, "orders.view");
  const { data, loading, error: loadError, reload } = useApi<ClientDebtDetail>(`/clients/${id}/debt-detail/`);
  const {
    data: history,
    error: historyError,
    reload: reloadHistory,
  } = useApi<ClientHistory>(canViewReports ? `/clients/${id}/history/` : null);
  const [tab, setTab] = useState("orders");
  const [notice, setNotice] = useState("");

  const payments = useMemo(() => history?.payments ?? [], [history]);
  const invoices = useMemo(() => payments.filter((p) => p.method === "invoice"), [payments]);

  if (!data) {
    return (
      <AppShell title="Долг клиента">
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );
  }

  // Итоги «за всё время» — та же сводка, что в карточке клиента (/history/).
  const summary = history?.summary;
  // Для просрочки сервер не выбирает валюту — выбираем здесь тем же правилом.
  const overdueByCurrency = data.overdue_by_currency;
  const overdueCurrency = primaryMoneyCurrency(overdueByCurrency, data.debt_currency);
  const overdueTotal = amountForCurrency(overdueByCurrency, overdueCurrency);

  function refresh() {
    void reload();
    if (canViewReports) void reloadHistory();
  }

  const orderContext: DebtOrderContext = {
    me,
    canViewOrder: canViewOrders,
    clientId: id,
    clientPhone: data.client.phone,
    // Магазин с расписанием блокирует оплату вне окна.
    blockedFor: (order) => blockingStore(order, data.stores),
    onPaid: (message) => {
      setNotice(message);
      refresh();
    },
  };

  return (
    <AppShell
      title={`Долг · ${data.client.name}`}
      section="Касса"
      actions={
        <Link href="/accounting?view=debts" className={buttonVariants({ size: "sm", variant: "outline" })}>
          <ArrowLeft className="size-4" />К долгам
        </Link>
      }
    >
      {/* Клиент и его деньги одной карточкой: кто, как связаться и сколько должен. */}
      <Card className="mb-5 flex flex-col gap-4 p-4">
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
          <div className="min-w-0">
            <div className="truncate text-lg font-semibold tracking-tight">{data.client.name}</div>
            {data.client.phone ? (
              <a
                href={`tel:${data.client.phone.replace(/[^\d+]/g, "")}`}
                className="inline-flex items-center gap-1.5 text-sm tabular-nums text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              >
                <Phone className="size-3.5" />
                {data.client.phone}
              </a>
            ) : (
              <div className="text-sm text-[var(--muted-foreground)]">Телефон не указан</div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            <div className="text-sm text-[var(--muted-foreground)]">
              Заказов в долге: <b className="tabular-nums text-[var(--foreground)]">{data.orders.length}</b>
            </div>
            {canViewReports && (
              <Link href={`/clients/${id}`} className={buttonVariants({ size: "sm", variant: "ghost" })}>
                Карточка клиента <ExternalLink className="size-3.5" />
              </Link>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-start gap-x-10 gap-y-3 border-t pt-4">
          <MoneyStat
            label="Текущий долг"
            amount={data.debt_total}
            currency={data.debt_currency}
            byCurrency={data.debt_by_currency}
            className="text-[var(--destructive)]"
          />
          {canViewReports && (
            <>
              <MoneyStat
                label="Просрочено"
                amount={overdueTotal}
                currency={overdueCurrency}
                byCurrency={overdueByCurrency}
                className="text-[var(--destructive)]"
              />
              {summary && (
                <>
                  <MoneyStat
                    label="Оплачено за всё время"
                    amount={summary.paid}
                    currency={summary.currency}
                    byCurrency={fieldByCurrency(summary.by_currency, "paid")}
                    className="text-[var(--success)]"
                  />
                  <MoneyStat
                    label="Сумма продаж за всё время"
                    amount={summary.revenue}
                    currency={summary.currency}
                    byCurrency={fieldByCurrency(summary.by_currency, "revenue")}
                  />
                </>
              )}
            </>
          )}
        </div>
      </Card>

      {notice && (
        <p
          role="status"
          className="mb-4 rounded-lg border border-[var(--success)]/30 bg-[var(--success)]/10 px-3 py-2 text-sm text-[var(--success)]"
        >
          {notice}
        </p>
      )}

      <div className="flex flex-col gap-4">
        <Tabs
          className="overflow-x-auto whitespace-nowrap"
          active={tab}
          onChange={setTab}
          tabs={[
            { key: "orders", label: "Заказы в долге", count: data.orders.length },
            ...(canViewReports
              ? [
                  { key: "history", label: "История платежей", count: payments.length },
                  { key: "invoices", label: "Счета", count: invoices.length },
                ]
              : []),
          ]}
        />

        {tab !== "orders" && historyError && <ErrorAlert message={historyError} onRetry={() => void reloadHistory()} />}
        {tab === "orders" &&
          (data.orders.length === 0 ? (
            <Card>
              <CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">
                Долгов нет.
              </CardContent>
            </Card>
          ) : (
            <DebtOrdersTable orders={data.orders} layout={mobile ? "cards" : "table"} ctx={orderContext} />
          ))}
        {tab === "history" && (
          <PaymentHistoryTable
            rows={payments}
            emptyText="Платежей пока нет."
            canViewOrders={canViewOrders}
            canManagePayments={can(me, "payments.confirm")}
            onChanged={refresh}
          />
        )}
        {tab === "invoices" && (
          <PaymentHistoryTable
            rows={invoices}
            emptyText="Выставленных счетов нет."
            canViewOrders={canViewOrders}
            canManagePayments={can(me, "payments.confirm")}
            onChanged={refresh}
          />
        )}
      </div>
    </AppShell>
  );
}

export default function ClientDebtPage(props: { params: Promise<{ id: string }> }) {
  return (
    <RequirePerm perm={["reports.view", "payments.create"]} title="Долг клиента">
      <ClientDebtPageInner {...props} />
    </RequirePerm>
  );
}
