"use client";
import { use, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { DataGate } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { ArrowLeft } from "lucide-react";
import type { Order, Store } from "@/lib/types";

const WEEKDAYS = ["", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
function describeSchedule(t: string, days: number[]): string {
  if (t === "none") return "Без расписания — оплата в любой день";
  if (t === "monthly") return days.length ? `Оплата по числам месяца: ${days.join(", ")}` : "Числа не заданы";
  return days.length ? `Оплата по дням недели: ${days.map((d) => WEEKDAYS[d] ?? d).join(", ")}` : "Дни не заданы";
}

interface StoreDebtDetail {
  store: Store;
  client_name: string;
  debt_total: string;
  window_open: boolean;
  orders: Order[];
}

function StoreDebtPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const isAccountant = can(me, "payments.create");
  const { data, loading, error: loadError, reload } = useApi<StoreDebtDetail>(`/stores/${id}/debt-detail/`);
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  if (!data)
    return (
      <AppShell title="Долг магазина">
        <DataGate loading={loading} error={loadError} onRetry={reload} />
      </AppShell>
    );

  const { store, orders } = data;
  const blocked = store.payment_schedule_type !== "none" && !data.window_open;

  async function pay(orderId: number) {
    const amount = amounts[orderId];
    if (!amount) return;
    setBusyId(orderId);
    setError("");
    setNotice("");
    try {
      await api.post(`/orders/${orderId}/payments/`, { amount });
      setAmounts((a) => ({ ...a, [orderId]: "" }));
      setNotice(
        `Оплата по заказу #${orderId} принята — долг спишется после сверки бухгалтером и подтверждения кассой.`,
      );
      await reload();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <AppShell
      title={`Долг · ${store.name}`}
      section="Касса"
      actions={
        <Link href="/accounting">
          <Button size="sm" variant="outline">
            <ArrowLeft className="size-4" /> К долгам
          </Button>
        </Link>
      }
    >
      <div className="mb-5 rounded-xl border bg-[var(--card)] p-5 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-lg font-bold tracking-tight">{store.name}</div>
            <div className="text-sm text-[var(--muted-foreground)]">{data.client_name}</div>
            <div className="mt-2 text-xs text-[var(--muted-foreground)]">
              {describeSchedule(store.payment_schedule_type, store.payment_days)}
            </div>
          </div>
          {store.payment_schedule_type !== "none" && (
            <Badge tone={data.window_open ? "warning" : "muted"} dot={data.window_open}>
              {data.window_open ? "Окно оплаты открыто сегодня" : "Окно оплаты закрыто"}
            </Badge>
          )}
        </div>
      </div>

      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Остаток долга" value={`${formatMoney(data.debt_total)} ₸`} />
        <StatCard label="Заказов в долге" value={String(orders.length)} />
        <StatCard
          label="Способ оплаты"
          value={store.payment_schedule_type === "none" ? "Свободно" : data.window_open ? "Доступна" : "Заблокирована"}
        />
      </section>

      {error && <p className="mb-4 text-sm text-[var(--destructive)]">{error}</p>}
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
                    <Link href={`/orders/${o.id}`} className="hover:underline">
                      Заказ #{o.id}
                    </Link>
                    <Badge tone={PAYMENT_STATUS_TONE[o.payment_status ?? "unpaid"] ?? "muted"} dot>
                      {PAYMENT_STATUS_LABELS[o.payment_status ?? "unpaid"] ?? o.payment_status}
                    </Badge>
                  </CardTitle>
                  <span className="text-sm tabular-nums text-[var(--muted-foreground)]">{o.truck_number || ""}</span>
                </CardHeader>
                <CardContent className="flex flex-col gap-3">
                  <div className="grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <span className="text-[var(--muted-foreground)]">Сумма</span>
                      <div className="tabular-nums font-medium">{formatMoney(o.total_amount)} ₸</div>
                    </div>
                    <div>
                      <span className="text-[var(--muted-foreground)]">Оплачено</span>
                      <div className="tabular-nums text-[var(--success)]">{formatMoney(o.paid_total)} ₸</div>
                    </div>
                    <div>
                      <span className="text-[var(--muted-foreground)]">Остаток</span>
                      <div className="tabular-nums font-medium text-[var(--destructive)]">
                        {formatMoney(String(remaining))} ₸
                      </div>
                    </div>
                  </div>
                  {(o.pending_payments?.length ?? 0) > 0 && (
                    <div className="flex items-center justify-between rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-xs">
                      <span className="text-[var(--warning)]">На подтверждении (бухгалтер → касса)</span>
                      <span className="tabular-nums font-semibold text-[var(--warning)]">
                        {formatMoney(String(o.pending_payments!.reduce((s, p) => s + Number(p.amount), 0)))} ₸
                      </span>
                    </div>
                  )}
                  {isAccountant && remaining > 0 && (
                    <div className="flex gap-2 border-t pt-3">
                      <Input
                        type="number"
                        placeholder="Сумма"
                        disabled={blocked}
                        value={amounts[o.id] ?? ""}
                        onChange={(e) => setAmounts((a) => ({ ...a, [o.id]: e.target.value }))}
                      />
                      <Button
                        size="sm"
                        disabled={blocked || busyId === o.id || !amounts[o.id]}
                        onClick={() => pay(o.id)}
                      >
                        Внести
                      </Button>
                    </div>
                  )}
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
    <RequirePerm perm="reports.view" title="Долг магазина">
      <StoreDebtPageInner {...props} />
    </RequirePerm>
  );
}
