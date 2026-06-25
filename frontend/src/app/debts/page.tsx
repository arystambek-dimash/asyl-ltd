"use client";
import { useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { Search, RefreshCw } from "lucide-react";
import type { Order } from "@/lib/types";

interface StoreDebt {
  store_id: number; store_name: string; client_id: number; client_name: string;
  payment_schedule_type: "none" | "monthly" | "weekly"; payment_days: number[];
  debt_total: string; orders_count: number; window_open: boolean; overdue: boolean;
}

const WEEKDAYS = ["", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
function describeSchedule(t: string, days: number[]): string {
  if (t === "none") return "Без расписания";
  if (t === "monthly") return days.length ? `Числа: ${days.join(", ")}` : "Числа не заданы";
  return days.length ? `Дни: ${days.map((d) => WEEKDAYS[d] ?? d).join(", ")}` : "Дни не заданы";
}

export default function DebtsPage() {
  const [tab, setTab] = useState<"orders" | "stores">("orders");
  const { data: orders, loading: lo, reload: reloadOrders } = useApi<Order[]>("/orders/debts/");
  const { data: storeDebts, loading: ls, reload: reloadStores } = useApi<StoreDebt[]>("/stores/debts/");
  const [q, setQ] = useState("");
  const [checkMsg, setCheckMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const orderList = orders ?? [];
  const storeList = storeDebts ?? [];
  const remainingOf = (o: Order) =>
    Number(o.remaining_amount ?? (Number(o.total_amount) - Number(o.paid_total)));

  const totalOrderDebt = orderList.reduce((s, o) => s + remainingOf(o), 0);
  const totalStoreDebt = storeList.reduce((s, r) => s + Number(r.debt_total), 0);
  const overdueCount = storeList.filter((r) => r.overdue).length;

  const filteredOrders = orderList.filter((o) =>
    !q || `${o.client_name ?? ""} ${o.id} ${o.truck_number ?? ""}`.toLowerCase().includes(q.toLowerCase()));
  const filteredStores = storeList.filter((r) =>
    !q || `${r.store_name} ${r.client_name}`.toLowerCase().includes(q.toLowerCase()));

  async function checkOverdue() {
    setBusy(true); setCheckMsg("");
    try {
      const r = await api.post<{ checked: number; overdue_notifications: number }>("/stores/check-overdue/");
      setCheckMsg(`Проверено магазинов: ${r.data.checked}. Просрочек: ${r.data.overdue_notifications}.`);
      reloadOrders(); reloadStores();
    } catch (e) { setCheckMsg(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Долги" section="Обзор" description="Непогашенные долги по заказам и магазинам."
      actions={
        <Button size="sm" variant="outline" disabled={busy} onClick={checkOverdue}>
          <RefreshCw className={"size-4" + (busy ? " animate-spin" : "")} />
          <span className="hidden sm:inline">Проверить просрочки</span>
        </Button>
      }>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-4">
        <StatCard label="Заказов в долге" value={String(orderList.length)} />
        <StatCard label="Долг по заказам" value={`${formatMoney(String(totalOrderDebt))} ₸`} />
        <StatCard label="Долг магазинов" value={`${formatMoney(String(totalStoreDebt))} ₸`} />
        <StatCard label="Просрочки" value={String(overdueCount)} />
      </section>

      {checkMsg && (
        <p className="mb-4 rounded-lg border bg-[var(--card)] px-4 py-2 text-sm text-[var(--muted-foreground)] shadow-card">
          {checkMsg}
        </p>
      )}

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="inline-flex rounded-lg border p-0.5">
          {([["orders", "По заказам"], ["stores", "По магазинам"]] as const).map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)}
              className={cn("rounded-md px-4 py-1.5 text-sm font-medium transition-colors",
                tab === k ? "bg-[var(--secondary)] text-[var(--foreground)]"
                  : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
              {label}
            </button>
          ))}
        </div>
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      {tab === "orders" ? (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead>
                <TR>
                  <TH>Заказ</TH><TH>Клиент</TH><TH>Сумма</TH><TH>Оплачено</TH>
                  <TH>Остаток</TH><TH>Статус</TH><TH></TH>
                </TR>
              </THead>
              <TBody>
                {lo ? (
                  <TR><TD colSpan={7} className="py-6 text-center text-[var(--muted-foreground)]">Загрузка…</TD></TR>
                ) : filteredOrders.length === 0 ? (
                  <TR><TD colSpan={7} className="py-6 text-center text-[var(--muted-foreground)]">Долгов нет.</TD></TR>
                ) : filteredOrders.map((o) => (
                  <TR key={o.id}>
                    <TD className="font-medium">#{o.id}
                      {o.truck_number && <span className="block text-xs text-[var(--muted-foreground)] tabular-nums">{o.truck_number}</span>}
                    </TD>
                    <TD>{o.client_name || "—"}</TD>
                    <TD className="tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums text-[var(--success)]">{formatMoney(o.paid_total)} ₸</TD>
                    <TD className="tabular-nums font-medium text-[var(--destructive)]">{formatMoney(String(remainingOf(o)))} ₸</TD>
                    <TD>
                      <Badge tone={PAYMENT_STATUS_TONE[o.payment_status ?? "unpaid"] ?? "muted"} dot>
                        {PAYMENT_STATUS_LABELS[o.payment_status ?? "unpaid"] ?? o.payment_status}
                      </Badge>
                    </TD>
                    <TD>
                      <div className="flex justify-end">
                        <Link href={`/orders/${o.id}`}><Button size="sm" variant="ghost">Открыть</Button></Link>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead>
                <TR>
                  <TH>Магазин</TH><TH>Клиент</TH><TH>Долг</TH><TH>Заказов</TH>
                  <TH>Расписание</TH><TH>Окно оплаты</TH><TH></TH>
                </TR>
              </THead>
              <TBody>
                {ls ? (
                  <TR><TD colSpan={7} className="py-6 text-center text-[var(--muted-foreground)]">Загрузка…</TD></TR>
                ) : filteredStores.length === 0 ? (
                  <TR><TD colSpan={7} className="py-6 text-center text-[var(--muted-foreground)]">Долгов по магазинам нет.</TD></TR>
                ) : filteredStores.map((r) => (
                  <TR key={r.store_id}>
                    <TD className="font-medium">{r.store_name}</TD>
                    <TD>{r.client_name}</TD>
                    <TD className="tabular-nums font-medium text-[var(--destructive)]">{formatMoney(r.debt_total)} ₸</TD>
                    <TD className="tabular-nums">{r.orders_count}</TD>
                    <TD className="text-xs text-[var(--muted-foreground)]">
                      {describeSchedule(r.payment_schedule_type, r.payment_days)}
                    </TD>
                    <TD>
                      {r.payment_schedule_type === "none" ? (
                        <Badge tone="muted">Всегда</Badge>
                      ) : r.window_open ? (
                        <Badge tone="warning" dot>Сегодня · ожидается оплата</Badge>
                      ) : (
                        <Badge tone="muted">Закрыто</Badge>
                      )}
                    </TD>
                    <TD>
                      <div className="flex justify-end">
                        <Link href={`/debts/stores/${r.store_id}`}><Button size="sm" variant="ghost">Открыть</Button></Link>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </AppShell>
  );
}
