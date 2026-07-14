"use client";
import { useMemo, useState } from "react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
} from "recharts";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { ErrorAlert } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";
import { formatMoney } from "@/lib/utils";
import { isFinancialOrderStatus, ORDER_STATUS_LABELS } from "@/lib/constants";
import { TrendingUp, Wallet, AlertCircle, ClipboardList, ArrowUpRight } from "lucide-react";
import type { Order, Payment } from "@/lib/types";

type Period = "week" | "month" | "year" | "all";
type Group = "day" | "week" | "month" | "year";

const PERIODS: { key: Period; label: string }[] = [
  { key: "week", label: "Неделя" }, { key: "month", label: "Месяц" },
  { key: "year", label: "Год" }, { key: "all", label: "Всё" },
];
const GROUPS: { key: Group; label: string }[] = [
  { key: "day", label: "По дням" }, { key: "week", label: "По неделям" },
  { key: "month", label: "По месяцам" }, { key: "year", label: "По годам" },
];

function isDebtOrder(o: Order): boolean {
  const remaining = Number(o.remaining_amount ?? (Number(o.total_amount) - Number(o.paid_total)));
  return o.is_debt ?? (o.status === "shipped" && o.settlement_intent === "debt" && remaining > 0);
}

function confirmedPayments(orders: Order[]): Payment[] {
  return orders.flatMap((order) => order.payments ?? [])
    .filter((payment) => payment.status === "confirmed");
}

// Ключ группировки + подпись для точки времени.
function bucket(iso: string, g: Group): { key: string; label: string } {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  if (g === "year") return { key: `${d.getFullYear()}`, label: `${d.getFullYear()}` };
  if (g === "month") return { key: `${d.getFullYear()}-${p(d.getMonth() + 1)}`, label: `${p(d.getMonth() + 1)}.${d.getFullYear()}` };
  if (g === "week") {
    const onejan = new Date(d.getFullYear(), 0, 1);
    const wk = Math.ceil(((d.getTime() - onejan.getTime()) / 86400000 + onejan.getDay() + 1) / 7);
    return { key: `${d.getFullYear()}-W${p(wk)}`, label: `н${wk} ${d.getFullYear()}` };
  }
  return { key: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`, label: `${p(d.getDate())}.${p(d.getMonth() + 1)}` };
}

function PillGroup<T extends string>({ items, active, onChange }:
  { items: { key: T; label: string }[]; active: T; onChange: (k: T) => void }) {
  return (
    <div className="inline-flex rounded-md border border-[var(--border)] bg-[var(--muted)] p-0.5">
      {items.map((it) => (
        <button key={it.key} type="button" onClick={() => onChange(it.key)}
          className={cn("h-7 rounded px-2.5 text-[13px] transition-colors",
            it.key === active
              ? "bg-[var(--card)] font-medium text-[var(--foreground)] shadow-sm"
              : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]")}>
          {it.label}
        </button>
      ))}
    </div>
  );
}

function MetricCard({ label, value, sub, icon: Icon, accent }: {
  label: string; value: string; sub: string; icon: React.ElementType; accent?: boolean;
}) {
  return (
    <div className={cn("flex flex-col gap-3 rounded-lg border p-5 transition-colors",
      accent ? "border-[var(--ring)]/20 bg-[var(--ring)]/10" : "border-[var(--border)] bg-[var(--card)]")}>
      <div className="flex items-start justify-between">
        <span className="text-[12px] font-medium text-[var(--muted-foreground)]">{label}</span>
        <Icon className="size-4 text-[var(--muted-foreground)]" />
      </div>
      <div className={cn("text-[26px] font-bold tabular-nums tracking-tight leading-none",
        accent && "text-[var(--ring)]")}>{value}</div>
      <span className="text-xs text-[var(--muted-foreground)]">{sub}</span>
    </div>
  );
}

function ReportsPageInner() {
  const { data: orders, error, reload } = useApi<Order[]>("/orders/");

  const [period, setPeriod] = useState<Period>("month");
  const [group, setGroup] = useState<Group>("day");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");

  // Границы периода: ручной диапазон переопределяет пресет.
  const range = useMemo(() => {
    if (from || to) {
      return { start: from ? new Date(from + "T00:00:00") : null, end: to ? new Date(to + "T23:59:59") : null };
    }
    if (period === "all") return { start: null as Date | null, end: null as Date | null };
    const end = new Date();
    const start = new Date();
    if (period === "week") start.setDate(end.getDate() - 7);
    else if (period === "month") start.setMonth(end.getMonth() - 1);
    else start.setFullYear(end.getFullYear() - 1);
    return { start, end };
  }, [period, from, to]);

  const inRange = (iso: string) => {
    const d = new Date(iso);
    if (range.start && d < range.start) return false;
    if (range.end && d > range.end) return false;
    return true;
  };

  const list = orders ?? [];
  const periodOrders = list.filter((o) => inRange(o.created_at));

  // KPI
  const revenue = periodOrders
    .filter((o) => isFinancialOrderStatus(o.status))
    .reduce((s, o) => s + Number(o.total_amount), 0);
  const payments = confirmedPayments(list);
  const received = payments
    .filter((payment) => inRange(payment.paid_at))
    .reduce((s, payment) => s + Number(payment.amount), 0);
  const debtors = list.filter((o) => isDebtOrder(o));
  const debtTotal = debtors.reduce(
    (s, o) => s + Number(o.remaining_amount ?? (Number(o.total_amount) - Number(o.paid_total))),
    0,
  );

  // Временной ряд: выручка по заказам + поступления по подтверждённым платежам.
  const series = useMemo(() => {
    const map: Record<string, { key: string; label: string; revenue: number; received: number }> = {};
    const touch = (iso: string) => {
      const b = bucket(iso, group);
      if (!map[b.key]) map[b.key] = { ...b, revenue: 0, received: 0 };
      return map[b.key];
    };
    periodOrders.filter((o) => isFinancialOrderStatus(o.status)).forEach((o) => {
      touch(o.created_at).revenue += Number(o.total_amount);
    });
    payments.filter((payment) => inRange(payment.paid_at)).forEach((payment) => {
      touch(payment.paid_at).received += Number(payment.amount);
    });
    return Object.values(map).sort((a, b) => a.key.localeCompare(b.key));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orders, group, range.start, range.end]);

  // По статусам заказов (в периоде)
  const byStatus = useMemo(() => {
    const m: Record<string, { count: number; sum: number }> = {};
    periodOrders.forEach((o) => {
      m[o.status] = m[o.status] || { count: 0, sum: 0 };
      m[o.status].count += 1;
      m[o.status].sum += Number(o.total_amount);
    });
    return Object.entries(m);
  }, [periodOrders]);

  return (
    <AppShell title="Отчёты" section="Обзор" description="Бухгалтерская аналитика: выручка, поступления и долги за период.">
      {/* Фильтры */}
      <div className="mb-5 flex flex-wrap items-end gap-x-6 gap-y-3">
        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] font-medium text-[var(--muted-foreground)]">Период</span>
          <PillGroup items={PERIODS} active={period} onChange={(k) => { setPeriod(k); setFrom(""); setTo(""); }} />
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] font-medium text-[var(--muted-foreground)]">Группировка</span>
          <PillGroup items={GROUPS} active={group} onChange={setGroup} />
        </div>
        <div className="flex items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-[var(--muted-foreground)]">С даты</span>
            <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="h-8 w-[150px]" />
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-[11px] font-medium text-[var(--muted-foreground)]">По дату</span>
            <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="h-8 w-[150px]" />
          </div>
        </div>
      </div>

      {error && !orders && <div className="mb-5"><ErrorAlert message={error} onRetry={reload} /></div>}

      {/* KPI */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Выручка" value={`${formatMoney(String(revenue))} ₸`} sub="заказов за период" icon={TrendingUp} accent />
        <MetricCard label="Поступило" value={`${formatMoney(String(received))} ₸`} sub="подтверждённых оплат за период" icon={Wallet} />
        <MetricCard label="Долг" value={`${formatMoney(String(debtTotal))} ₸`} sub={`${debtors.length} заказов`} icon={AlertCircle} />
        <MetricCard label="Заказов" value={String(periodOrders.length)} sub="за период" icon={ClipboardList} />
      </div>

      {/* График выручка/поступления */}
      <Card className="mt-6 rounded-2xl">
        <CardHeader><CardTitle>Выручка и поступления</CardTitle></CardHeader>
        <CardContent>
          {series.length === 0 ? (
            <p className="py-16 text-center text-sm text-[var(--muted-foreground)]">Нет данных за выбранный период.</p>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <AreaChart data={series} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="rev" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--ring)" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="var(--ring)" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="rcv" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="var(--success)" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="var(--success)" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false}
                  width={52} tickFormatter={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(v))} />
                <Tooltip contentStyle={{ background: "var(--card)", borderRadius: 12, border: "1px solid var(--border)", fontSize: 12 }}
                  formatter={(v: number, n) => [`${formatMoney(String(v))} ₸`, n === "revenue" ? "Выручка" : "Поступления"]} />
                <Area type="monotone" dataKey="revenue" stroke="var(--ring)" strokeWidth={2.5} fill="url(#rev)" />
                <Area type="monotone" dataKey="received" stroke="var(--success)" strokeWidth={2.5} fill="url(#rcv)" />
              </AreaChart>
            </ResponsiveContainer>
          )}
          <div className="mt-2 flex gap-4 text-xs">
            <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full" style={{ background: "var(--ring)" }} /> Выручка</span>
            <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full" style={{ background: "var(--success)" }} /> Поступления</span>
          </div>
        </CardContent>
      </Card>

      {/* По статусам + долги */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card className="rounded-2xl">
          <CardHeader><CardTitle>По статусам заказов</CardTitle></CardHeader>
          <CardContent>
            {byStatus.length === 0 ? (
              <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Нет заказов за период.</p>
            ) : (
              <Table>
                <THead><TR><TH>Статус</TH><TH>Кол-во</TH><TH>Сумма</TH></TR></THead>
                <TBody>
                  {byStatus.map(([st, v]) => (
                    <TR key={st}>
                      <TD>{ORDER_STATUS_LABELS[st] ?? st}</TD>
                      <TD className="tabular-nums">{v.count}</TD>
                      <TD className="tabular-nums">{formatMoney(String(v.sum))} ₸</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card className="rounded-2xl">
          <CardHeader className="flex-row items-center gap-2">
            <AlertCircle className="size-4 text-[var(--muted-foreground)]" />
            <CardTitle>Дебиторская задолженность</CardTitle>
          </CardHeader>
          <CardContent>
            {debtors.length === 0 ? (
              <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Долгов нет.</p>
            ) : (
              <div className="flex flex-col gap-4">
                <div className="flex items-end justify-between">
                  <div>
                    <div className="text-sm text-[var(--muted-foreground)]">Итого долг</div>
                    <div className="text-2xl font-bold tabular-nums text-[var(--destructive)]">{formatMoney(String(debtTotal))} ₸</div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm text-[var(--muted-foreground)]">Заказов в долге</div>
                    <div className="text-2xl font-bold tabular-nums">{debtors.length}</div>
                  </div>
                </div>
                <Link href="/accounting?tab=debts">
                  <Button variant="outline" className="w-full">
                    Перейти к долгам <ArrowUpRight className="size-4" />
                  </Button>
                </Link>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}

export default function ReportsPage() {
  return <RequirePerm perm="reports.view" title="Отчёты"><ReportsPageInner /></RequirePerm>;
}
