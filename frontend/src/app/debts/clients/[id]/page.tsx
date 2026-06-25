"use client";
import { use, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { can } from "@/lib/can";
import { useAuth } from "@/store/auth";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { ArrowLeft, ArrowUpRight } from "lucide-react";
import type { Client, Order } from "@/lib/types";

interface DebtStore {
  id: number;
  name: string;
  payment_schedule_type: "none" | "monthly" | "weekly";
  payment_days: number[];
  window_open: boolean;
}

interface ClientDebtDetail {
  client: Client;
  debt_total: string;
  orders_count: number;
  unpaid_count: number;
  partial_count: number;
  stores: DebtStore[];
  orders: Order[];
}

const WEEKDAYS = ["", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
function scheduleLabel(store: DebtStore) {
  if (store.payment_schedule_type === "none") return "Свободная оплата";
  if (store.payment_schedule_type === "monthly") {
    return store.payment_days.length ? `Числа: ${store.payment_days.join(", ")}` : "Числа не заданы";
  }
  return store.payment_days.length
    ? `Дни: ${store.payment_days.map((d) => WEEKDAYS[d] ?? d).join(", ")}`
    : "Дни не заданы";
}

export default function ClientDebtPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const isAccountant = can(me, "payments.create");
  const { data, reload } = useApi<ClientDebtDetail>(`/clients/${id}/debt-detail/`);
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");

  if (!data) {
    return (
      <AppShell title="Долг клиента">
        <p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      </AppShell>
    );
  }

  const storeById = new Map(data.stores.map((s) => [s.id, s]));
  // Магазин с расписанием блокирует оплату вне окна.
  function blockedFor(order: Order): DebtStore | null {
    if (order.store == null) return null;
    const s = storeById.get(order.store);
    if (!s || s.payment_schedule_type === "none" || s.window_open) return null;
    return s;
  }

  async function pay(order: Order, channel: "manual" | "bank") {
    setBusyId(order.id); setError("");
    try {
      if (channel === "bank") {
        await api.post(`/orders/${order.id}/pay-bank/`);
      } else {
        const amount = amounts[order.id];
        if (!amount) { setBusyId(null); return; }
        await api.post(`/orders/${order.id}/payments/`, { amount });
        setAmounts((a) => ({ ...a, [order.id]: "" }));
      }
      await reload();
    } catch (e) { setError(apiError(e)); } finally { setBusyId(null); }
  }

  return (
    <AppShell title={`Долг · ${data.client.name}`} section="Обзор"
      actions={
        <Link href="/debts">
          <Button size="sm" variant="outline">
            <ArrowLeft className="size-4" />
            К долгам
          </Button>
        </Link>
      }>
      <div className="mb-5 rounded-xl border bg-[var(--card)] p-5 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-lg font-bold tracking-tight">{data.client.name}</div>
            <div className="text-sm text-[var(--muted-foreground)]">{data.client.phone || "—"}</div>
          </div>
          <Badge tone={data.partial_count > 0 ? "warning" : "destructive"} dot>
            {data.partial_count > 0 ? "Есть частичная оплата" : "Не оплачен"}
          </Badge>
        </div>
        <p className="mt-3 border-t pt-3 text-sm text-[var(--muted-foreground)]">
          {isAccountant
            ? "Внесите оплату по каждому заказу ниже. Долг гасится частями или полностью."
            : "Долги клиента по отгруженным заказам. Оплату вносит бухгалтер."}
        </p>
      </div>

      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-4">
        <StatCard label="Остаток клиента" value={`${formatMoney(data.debt_total)} ₸`} accent />
        <StatCard label="Заказов в долге" value={String(data.orders_count)} />
        <StatCard label="Без оплат" value={String(data.unpaid_count)} />
        <StatCard label="Частично оплачено" value={String(data.partial_count)} />
      </section>

      {data.stores.length > 0 && (
        <div className="mb-5 grid grid-cols-1 gap-3 lg:grid-cols-2">
          {data.stores.map((store) => (
            <Card key={store.id}>
              <CardHeader className="flex-row items-center justify-between gap-3 pb-3">
                <CardTitle className="text-base">{store.name}</CardTitle>
                {store.payment_schedule_type !== "none" && (
                  <Badge tone={store.window_open ? "warning" : "muted"} dot={store.window_open}>
                    {store.window_open ? "Окно оплаты открыто" : "Окно закрыто"}
                  </Badge>
                )}
              </CardHeader>
              <CardContent className="text-sm text-[var(--muted-foreground)]">
                {scheduleLabel(store)}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {error && <p className="mb-4 text-sm text-[var(--destructive)]">{error}</p>}

      <div className="mb-2 text-lg font-semibold tracking-tight">Заказы в долге</div>
      <div className="flex flex-col gap-3">
        {data.orders.length === 0 ? (
          <Card><CardContent className="py-10 text-center text-sm text-[var(--muted-foreground)]">Долгов нет.</CardContent></Card>
        ) : data.orders.map((order) => {
          const status = order.payment_status ?? "unpaid";
          const remaining = Number(order.remaining_amount ?? (Number(order.total_amount) - Number(order.paid_total)));
          const blockingStore = blockedFor(order);
          return (
            <Card key={order.id}>
              <CardHeader className="flex-row items-center justify-between gap-2 pb-3">
                <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                  <span>Заказ #{order.id}</span>
                  <Badge tone={PAYMENT_STATUS_TONE[status] ?? "muted"} dot>
                    {PAYMENT_STATUS_LABELS[status] ?? status}
                  </Badge>
                  {order.truck_number && (
                    <span className="text-sm font-normal tabular-nums text-[var(--muted-foreground)]">{order.truck_number}</span>
                  )}
                </CardTitle>
                <Link href={`/orders/${order.id}`}>
                  <Button size="sm" variant="ghost">Заказ <ArrowUpRight className="size-4" /></Button>
                </Link>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <div className="grid grid-cols-3 gap-2 text-sm">
                  <div><span className="text-[var(--muted-foreground)]">Сумма</span><div className="tabular-nums font-medium">{formatMoney(order.total_amount)} ₸</div></div>
                  <div><span className="text-[var(--muted-foreground)]">Оплачено</span><div className="tabular-nums text-[var(--success)]">{formatMoney(order.paid_total)} ₸</div></div>
                  <div><span className="text-[var(--muted-foreground)]">Остаток</span><div className="tabular-nums font-semibold text-[var(--destructive)]">{formatMoney(String(remaining))} ₸</div></div>
                </div>

                {/* история платежей */}
                {(order.payments?.length ?? 0) > 0 && (
                  <div className="flex flex-col gap-1 border-t pt-2 text-xs">
                    {order.payments!.map((p) => (
                      <div key={p.id} className="flex justify-between text-[var(--muted-foreground)]">
                        <span>
                          {new Date(p.paid_at).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" })}
                          {" · "}{p.method_label ?? p.method}
                          {p.recorded_by_name ? ` · ${p.recorded_by_name}` : ""}
                        </span>
                        <span className="tabular-nums text-[var(--success)]">+{formatMoney(p.amount)} ₸</span>
                      </div>
                    ))}
                  </div>
                )}

                {isAccountant && remaining > 0 && (
                  blockingStore ? (
                    <p className="border-t pt-3 text-xs text-[var(--warning)]">
                      Оплата заблокирована: магазин «{blockingStore.name}» платит только по расписанию ({scheduleLabel(blockingStore)}).
                    </p>
                  ) : (
                    <div className="flex flex-col gap-2 border-t pt-3">
                      <div className="flex gap-2">
                        <Input type="number" placeholder="Сумма" value={amounts[order.id] ?? ""}
                          onChange={(e) => setAmounts((a) => ({ ...a, [order.id]: e.target.value }))} />
                        <Button size="sm" disabled={busyId === order.id || !amounts[order.id]}
                          onClick={() => pay(order, "manual")}>Внести</Button>
                      </div>
                      {order.settlement_intent === "instant" && (
                        <Button size="sm" variant="outline" disabled={busyId === order.id}
                          onClick={() => pay(order, "bank")}>
                          Оплатить через банк ({formatMoney(String(remaining))} ₸)
                        </Button>
                      )}
                    </div>
                  )
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </AppShell>
  );
}
