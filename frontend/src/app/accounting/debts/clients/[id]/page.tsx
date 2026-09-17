"use client";
import { Fragment, use, useMemo, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { PaymentHistoryTable, type HistoryPayment } from "@/components/payment-history-table";
import { Tabs } from "@/components/ui/tabs";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { useApi } from "@/lib/use-api";
import { useIsMobile } from "@/lib/use-media-query";
import { withBack } from "@/lib/navigation";
import { amountForCurrency, otherCurrencyAmounts, primaryMoneyCurrency } from "@/lib/currency-map";
import { cn, formatCompactCurrency, formatCurrency, formatDateTime } from "@/lib/utils";
import { can } from "@/lib/can";
import { PaidMethodBreakdown } from "@/components/payment-chain";
import { OrderPaymentActions } from "@/components/payments/order-payment-actions";
import { useAuth } from "@/store/auth";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE, PAYMENT_STAGE_LABELS, PAYMENT_STAGE_TONE } from "@/lib/constants";
import { ArrowLeft, ChevronDown, ExternalLink, Info, Phone } from "lucide-react";
import type { Me, Order } from "@/lib/types";
import { formatPaymentSchedule } from "@/app/stores/schedule-validation";
import { blockingStore, pendingSum, remainingOf, type ClientDebtDetail, type DebtStore } from "@/lib/debt-orders";

const money = formatCurrency;
const compactMoney = formatCompactCurrency;

function CurrencyRows({ totals, primary }: { totals: Record<string, string | number>; primary: string }) {
  const rows = otherCurrencyAmounts(totals, primary);
  if (rows.length === 0) return null;
  return (
    <div className="grid gap-0.5 text-xs text-[var(--muted-foreground)]">
      {rows.map(([currency, amount]) => (
        <span key={currency}>Также {money(amount, currency)}</span>
      ))}
    </div>
  );
}

interface ClientHistory {
  payments: HistoryPayment[];
}

/* ── Счёт по заказу: зафиксированные клиентские цены ───────────────────── */
function InvoiceTable({ order, layout = "full" }: { order: Order; layout?: "full" | "compact" }) {
  const lines = order.items.map((it) => {
    const price = Number(it.price ?? 0);
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
                  {l.qty} × {money(l.price, order.currency)}
                </div>
              </div>
              <div className="font-medium tabular-nums">{money(l.total, order.currency)}</div>
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
                <TD className="text-right tabular-nums">{money(l.price, order.currency)}</TD>
                <TD className="text-right tabular-nums font-medium">{money(l.total, order.currency)}</TD>
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
          <span className="tabular-nums">{money(total, order.currency)}</span>
        </div>
        {paid > 0 && (
          <div className="flex justify-between text-[var(--success)]">
            <span>Уже оплачено</span>
            <span className="tabular-nums">−{money(paid, order.currency)}</span>
          </div>
        )}
        <div className="flex justify-between text-base font-semibold">
          <span>Остаток к оплате</span>
          <span className="tabular-nums">{money(toPay, order.currency)}</span>
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
      {rows.map((p) => (
        <div key={p.id} className="flex items-center justify-between gap-3 py-2.5 text-sm">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="tabular-nums">{formatDateTime(p.paid_at)}</span>
              <span className="text-[var(--muted-foreground)]">{p.method_label ?? p.method}</span>
              <Badge tone={PAYMENT_STAGE_TONE[p.status] ?? "muted"}>{PAYMENT_STAGE_LABELS[p.status] ?? p.status}</Badge>
            </div>
            {(p.recorded_by_name || p.note) && (
              <div className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">
                {[p.recorded_by_name, p.note].filter(Boolean).join(" · ")}
              </div>
            )}
          </div>
          <span
            className={cn(
              "shrink-0 tabular-nums font-semibold",
              p.status === "confirmed" ? "text-[var(--success)]" : "text-[var(--muted-foreground)]",
            )}
          >
            +{money(p.amount, p.currency ?? order.currency)}
          </span>
        </div>
      ))}
    </div>
  );
}

/* ── Карточка заказа в долге ────────────────────────────────────────────── */
/* Детали раскрытой строки: резерв, позиции счёта и списание. */
function DebtOrderDetails({ order, layout = "full" }: { order: Order; layout?: "full" | "compact" }) {
  const [tab, setTab] = useState("invoice");
  const pending = pendingSum(order);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--muted-foreground)]">
        {order.department && <span>{order.department_name ?? order.department}</span>}
        {order.truck_number && <span className="tabular-nums">{order.truck_number}</span>}
        <span>
          Сумма заказа: <b className="tabular-nums">{money(order.total_amount, order.currency)}</b>
        </span>
        <PaidMethodBreakdown order={order} className="text-xs" />
      </div>
      {pending > 0 && (
        <div className="flex items-center justify-between rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-sm">
          <span className="flex items-center gap-1.5 text-[var(--warning)]">
            <Info className="size-4" />
            Уже зарезервировано и ожидает оплаты либо подтверждения
          </span>
          <span className="tabular-nums font-semibold text-[var(--warning)]">{money(pending, order.currency)}</span>
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
        blockedReason={
          store ? `Магазин «${store.name}» платит по расписанию: ${formatPaymentSchedule(store, "payment")}` : null
        }
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
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
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
            const status = order.payment_status ?? "unpaid";
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
                      <span className="text-xs text-[var(--muted-foreground)]">
                        {order.department_name ?? order.department}
                      </span>
                    </div>
                    <Badge tone={PAYMENT_STATUS_TONE[status] ?? "muted"} dot>
                      {PAYMENT_STATUS_LABELS[status] ?? status}
                    </Badge>
                  </div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    Создан {formatDateTime(order.created_at)} · Отгружен{" "}
                    {order.shipped_at ? formatDateTime(order.shipped_at) : "—"}
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <div>
                      <div className="text-[11px] text-[var(--muted-foreground)]">Оплачено</div>
                      <div className="tabular-nums text-[var(--success)]">
                        {money(order.paid_total, order.currency)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[11px] text-[var(--muted-foreground)]">Остаток</div>
                      <div className="font-semibold tabular-nums text-[var(--destructive)]">
                        {money(remainingOf(order), order.currency)}
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
            const status = order.payment_status ?? "unpaid";
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
                    <Badge tone={PAYMENT_STATUS_TONE[status] ?? "muted"} dot>
                      {PAYMENT_STATUS_LABELS[status] ?? status}
                    </Badge>
                  </TD>
                  <TD className="text-right tabular-nums text-[var(--success)]">
                    {money(order.paid_total, order.currency)}
                  </TD>
                  <TD className="text-right tabular-nums font-semibold text-[var(--destructive)]">
                    {money(remainingOf(order), order.currency)}
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

  const debtByCurrency = data.debt_by_currency ?? {};
  const debtCurrency = primaryMoneyCurrency(debtByCurrency, data.debt_currency ?? data.client.currency);
  const debtTotal = amountForCurrency(debtByCurrency, data.debt_total, debtCurrency);
  const lifetimeTotalByCurrency = Object.fromEntries(
    Object.entries(data.lifetime_by_currency ?? {}).map(([currency, totals]) => [currency, totals.total]),
  );
  const lifetimePaidByCurrency = Object.fromEntries(
    Object.entries(data.lifetime_by_currency ?? {}).map(([currency, totals]) => [currency, totals.paid]),
  );
  const lifetimeCurrency = primaryMoneyCurrency(lifetimeTotalByCurrency, debtCurrency);
  const paidCurrency = primaryMoneyCurrency(lifetimePaidByCurrency, lifetimeCurrency);
  const lifetimeTotal = amountForCurrency(lifetimeTotalByCurrency, data.lifetime_total ?? "0", lifetimeCurrency);
  const lifetimePaid = amountForCurrency(lifetimePaidByCurrency, data.lifetime_paid ?? "0", paidCurrency);
  const overdueByCurrency = data.overdue_by_currency ?? {};
  const overdueCurrency = primaryMoneyCurrency(overdueByCurrency, debtCurrency);
  const overdueTotal = amountForCurrency(overdueByCurrency, data.overdue_total ?? "0", overdueCurrency);

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
          <div className="text-sm text-[var(--muted-foreground)]">
            Заказов в долге: <b className="tabular-nums text-[var(--foreground)]">{data.orders.length}</b>
          </div>
        </div>
        <div className="flex flex-wrap items-start gap-x-10 gap-y-3 border-t pt-4">
          <div className="min-w-0">
            <div className="text-xs text-[var(--muted-foreground)]">Текущий долг</div>
            <div
              title={money(debtTotal, debtCurrency)}
              className="mt-1 truncate text-lg font-semibold leading-none tabular-nums text-[var(--destructive)]"
            >
              {compactMoney(debtTotal, debtCurrency)}
            </div>
            <CurrencyRows totals={debtByCurrency} primary={debtCurrency} />
          </div>
          {canViewReports && (
            <>
              <div className="min-w-0">
                <div className="text-xs text-[var(--muted-foreground)]">Просрочено</div>
                <div
                  title={money(overdueTotal, overdueCurrency)}
                  className="mt-1 truncate text-lg font-semibold leading-none tabular-nums text-[var(--destructive)]"
                >
                  {compactMoney(overdueTotal, overdueCurrency)}
                </div>
                <CurrencyRows totals={overdueByCurrency} primary={overdueCurrency} />
              </div>
              <div className="min-w-0">
                <div className="text-xs text-[var(--muted-foreground)]">Оплачено за всё время</div>
                <div
                  title={money(lifetimePaid, paidCurrency)}
                  className="mt-1 truncate text-lg font-semibold leading-none tabular-nums text-[var(--success)]"
                >
                  {compactMoney(lifetimePaid, paidCurrency)}
                </div>
                <CurrencyRows totals={lifetimePaidByCurrency} primary={paidCurrency} />
              </div>
              <div className="min-w-0">
                <div className="text-xs text-[var(--muted-foreground)]">Задолженность за всё время</div>
                <div
                  title={money(lifetimeTotal, lifetimeCurrency)}
                  className="mt-1 truncate text-lg font-semibold leading-none tabular-nums"
                >
                  {compactMoney(lifetimeTotal, lifetimeCurrency)}
                </div>
                <CurrencyRows totals={lifetimeTotalByCurrency} primary={lifetimeCurrency} />
              </div>
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

      <div className="grid grid-cols-1 items-start gap-5">
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

          {tab !== "orders" && historyError && (
            <ErrorAlert message={historyError} onRetry={() => void reloadHistory()} />
          )}
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
