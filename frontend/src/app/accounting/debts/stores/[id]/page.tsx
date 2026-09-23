"use client";
import { use, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { OrderPaymentActions } from "@/components/payments/order-payment-actions";
import { StatCard } from "@/components/ui/stat-card";
import { DataGate } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { withBack } from "@/lib/navigation";
import { amountForCurrency, otherCurrencyAmounts, primaryMoneyCurrency } from "@/lib/currency-map";
import { formatCurrency } from "@/lib/utils";
import { orderTransportText } from "@/lib/wagons";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { ArrowLeft } from "lucide-react";
import type { Order, Store } from "@/lib/types";
import { formatPaymentSchedule } from "@/app/stores/schedule-validation";

interface StoreDebtDetail {
  store: Store;
  client_name: string;
  /** Долг в основной валюте магазина. Валюты не складываются. */
  debt_total: string;
  debt_currency?: "KZT" | "USD";
  debt_by_currency?: Record<string, string>;
  window_open: boolean;
  orders: Order[];
}

function StoreDebtPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const canViewOrders = can(me, "orders.view");
  const { data, loading, error: loadError, reload } = useApi<StoreDebtDetail>(`/stores/${id}/debt-detail/`);
  const [notice, setNotice] = useState("");

  if (!data)
    return (
      <AppShell title="Долг магазина">
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );

  const { store, orders } = data;
  const blocked = store.payment_schedule_type !== "none" && !data.window_open;
  const debtByCurrency = data.debt_by_currency ?? {};
  const debtCurrency = primaryMoneyCurrency(debtByCurrency, data.debt_currency ?? "KZT");
  const debtTotal = amountForCurrency(debtByCurrency, data.debt_total, debtCurrency);
  const otherDebts = otherCurrencyAmounts(debtByCurrency, debtCurrency);

  return (
    <AppShell
      title={`Долг · ${store.name}`}
      section="Касса"
      actions={
        <Link href="/accounting?view=debts" className={buttonVariants({ size: "sm", variant: "outline" })}>
          <ArrowLeft className="size-4" /> К долгам
        </Link>
      }
    >
      <div className="mb-5 rounded-xl border bg-[var(--card)] p-5 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-lg font-bold tracking-tight">{store.name}</div>
            <div className="text-sm text-[var(--muted-foreground)]">{data.client_name}</div>
            <div className="mt-2 text-xs text-[var(--muted-foreground)]">{formatPaymentSchedule(store, "detail")}</div>
          </div>
          {store.payment_schedule_type !== "none" && (
            <Badge tone={data.window_open ? "warning" : "muted"} dot={data.window_open}>
              {data.window_open ? "Окно оплаты открыто сегодня" : "Окно оплаты закрыто"}
            </Badge>
          )}
        </div>
      </div>

      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Остаток долга" value={formatCurrency(debtTotal, debtCurrency)}>
          {otherDebts.length > 0 && (
            <div className="grid gap-0.5 text-xs text-[var(--muted-foreground)]">
              {otherDebts.map(([currency, amount]) => (
                <span key={currency}>Также {formatCurrency(amount, currency)}</span>
              ))}
            </div>
          )}
        </StatCard>
        <StatCard label="Заказов в долге" value={String(orders.length)} />
        <StatCard
          label="Способ оплаты"
          value={store.payment_schedule_type === "none" ? "Свободно" : data.window_open ? "Доступна" : "Заблокирована"}
        />
      </section>

      {notice && (
        <p className="mb-4 rounded-lg border border-[var(--success)]/30 bg-[var(--success)]/10 px-3 py-2 text-sm text-[var(--success)]">
          {notice}
        </p>
      )}
      {blocked && (
        <p className="mb-4 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-4 py-2 text-sm text-[var(--warning)]">
          Оплата заблокирована — сегодня не день оплаты по расписанию магазина.
        </p>
      )}

      <div className="flex flex-col gap-3">
        {orders.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">Долгов нет.</CardContent>
          </Card>
        ) : (
          orders.map((o) => {
            const remaining = Number(o.remaining_amount ?? Number(o.total_amount) - Number(o.paid_total));
            return (
              <Card key={o.id}>
                <CardHeader className="flex-row items-center justify-between gap-2 pb-3">
                  <CardTitle className="flex items-center gap-2 text-base">
                    {canViewOrders ? (
                      <Link
                        href={withBack(`/orders/${o.id}`, `/accounting/debts/stores/${id}`)}
                        className="hover:underline"
                      >
                        Заказ #{o.id}
                      </Link>
                    ) : (
                      <span>Заказ #{o.id}</span>
                    )}
                    <Badge tone={PAYMENT_STATUS_TONE[o.payment_status ?? "unpaid"] ?? "muted"} dot>
                      {PAYMENT_STATUS_LABELS[o.payment_status ?? "unpaid"] ?? o.payment_status}
                    </Badge>
                  </CardTitle>
                  <span className="text-sm tabular-nums text-[var(--muted-foreground)]">{orderTransportText(o)}</span>
                </CardHeader>
                <CardContent className="flex flex-col gap-3">
                  <div className="grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <span className="text-[var(--muted-foreground)]">Сумма</span>
                      <div className="tabular-nums font-medium">{formatCurrency(o.total_amount, o.currency)}</div>
                    </div>
                    <div>
                      <span className="text-[var(--muted-foreground)]">Оплачено</span>
                      <div className="tabular-nums text-[var(--success)]">
                        {formatCurrency(o.paid_total, o.currency)}
                      </div>
                    </div>
                    <div>
                      <span className="text-[var(--muted-foreground)]">Остаток</span>
                      <div className="tabular-nums font-medium text-[var(--destructive)]">
                        {formatCurrency(String(remaining), o.currency)}
                      </div>
                    </div>
                  </div>
                  {(o.pending_payments?.length ?? 0) > 0 && (
                    <div className="flex items-center justify-between rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-xs">
                      <span className="text-[var(--warning)]">Ожидает поступления или подтверждения</span>
                      <span className="tabular-nums font-semibold text-[var(--warning)]">
                        {formatCurrency(
                          String(o.pending_payments!.reduce((s, p) => s + Number(p.amount), 0)),
                          o.currency,
                        )}
                      </span>
                    </div>
                  )}
                  <OrderPaymentActions
                    order={o}
                    me={me}
                    className="border-t pt-3"
                    blockedReason={blocked ? "Сегодня не день оплаты по расписанию магазина" : null}
                    onChanged={(message) => {
                      setNotice(message);
                      void reload();
                    }}
                  />
                </CardContent>
              </Card>
            );
          })
        )}
      </div>
    </AppShell>
  );
}

export default function StoreDebtPage(props: { params: Promise<{ id: string }> }) {
  return (
    <RequirePerm perm={["reports.view", "payments.create"]} title="Долг магазина">
      <StoreDebtPageInner {...props} />
    </RequirePerm>
  );
}
