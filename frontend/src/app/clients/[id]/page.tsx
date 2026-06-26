"use client";
import { use } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid,
  PieChart, Pie, Cell,
} from "recharts";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { formatMoney } from "@/lib/utils";
import {
  ArrowLeft, Phone, CircleDollarSign, Wallet, TrendingDown, BarChart3,
  ClipboardList, AlertTriangle, ArrowUpRight,
} from "lucide-react";
import type { Order } from "@/lib/types";

const STATUS_COLORS: Record<string, string> = {
  draft: "#8a8f98", pending: "#d49a32", confirmed: "#477fca", arrived: "#2f9ab7",
  loading: "#d28a28", loaded: "#5b67c9", shipped: "#5aa060", rejected: "#d85d57", cancelled: "#6f737a",
};

interface Analytics {
  client: { id: number; name: string; phone: string; country: string };
  kpi: { revenue: string; paid: string; debt: string; average: string; orders_count: number; rejected_count: number };
  by_status: { status: string; label: string; count: number; amount: string }[];
  monthly: { month: string; revenue: string; paid: string }[];
  top_products: { product: number; label: string; qty: number; amount: string }[];
  recent_orders: Order[];
}

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
}
function monthLabel(key: string): string {
  const [y, m] = key.split("-");
  return new Date(Number(y), Number(m) - 1).toLocaleDateString("ru-RU", { month: "short", year: "2-digit" });
}

function ClientDetailPageInner({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { me } = useAuth();
  const canMoney = can(me, "reports.view");  // финансовые блоки — под reports.view
  const { data } = useApi<Analytics>(`/clients/${id}/analytics/`);

  if (!data) {
    return <AppShell title="Клиент"><p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p></AppShell>;
  }

  const { client, kpi } = data;
  const hasDebt = Number(kpi.debt) > 0;
  const monthData = data.monthly.map((m) => ({ ...m, label: monthLabel(m.month), revenue: Number(m.revenue), paid: Number(m.paid) }));
  const maxProductAmount = Math.max(1, ...data.top_products.map((p) => Number(p.amount)));

  return (
    <AppShell title={`Клиент · ${client.name}`} section="Работа"
      actions={
        <Link href="/clients"><Button size="sm" variant="outline"><ArrowLeft className="size-4" /> К клиентам</Button></Link>
      }>
      {/* шапка */}
      <div className="mb-5 rounded-xl border bg-[var(--card)] p-5 shadow-card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-[var(--ring)] text-base font-semibold text-white">
              {initials(client.name)}
            </div>
            <div>
              <div className="text-lg font-bold tracking-tight">{client.name}</div>
              <div className="flex items-center gap-3 text-sm text-[var(--muted-foreground)]">
                <span className="flex items-center gap-1.5"><Phone className="size-3.5" /> {client.phone || "—"}</span>
                {client.country && <span>{client.country}</span>}
              </div>
            </div>
          </div>
          <Badge tone={hasDebt ? "destructive" : "success"} dot>
            {hasDebt ? `Долг ${formatMoney(kpi.debt)} ₸` : "Без долга"}
          </Badge>
        </div>
      </div>

      {/* KPI — финансовые показаны только при reports.view */}
      <section className={`mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 ${canMoney ? "lg:grid-cols-6" : "lg:grid-cols-3"}`}>
        {canMoney && <StatCard label="Принёс" value={`${formatMoney(kpi.revenue)} ₸`} accent icon={CircleDollarSign} />}
        {canMoney && <StatCard label="Оплачено" value={`${formatMoney(kpi.paid)} ₸`} icon={Wallet} />}
        <StatCard label="Текущий долг" value={`${formatMoney(kpi.debt)} ₸`} icon={TrendingDown} />
        {canMoney && <StatCard label="Средний чек" value={`${formatMoney(kpi.average)} ₸`} icon={BarChart3} />}
        <StatCard label="Заказов" value={String(kpi.orders_count)} icon={ClipboardList} />
        <StatCard label="Отклонённые" value={String(kpi.rejected_count)} icon={AlertTriangle} />
      </section>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {/* динамика — финансовая, под reports.view */}
        {canMoney && (
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">Динамика по месяцам</CardTitle>
            <BarChart3 className="size-4 text-[var(--muted-foreground)]" />
          </CardHeader>
          <CardContent>
            {monthData.length === 0 ? (
              <p className="py-10 text-center text-sm text-[var(--muted-foreground)]">Нет финансовых заказов.</p>
            ) : (
              <>
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={monthData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                    <XAxis dataKey="label" tick={{ fontSize: 12, fill: "var(--muted-foreground)" }} />
                    <YAxis tick={{ fontSize: 12, fill: "var(--muted-foreground)" }} width={70}
                      tickFormatter={(v) => formatMoney(String(v))} />
                    <Tooltip cursor={{ fill: "var(--muted)", opacity: 0.4 }}
                      contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
                      formatter={(v: number, n) => [`${formatMoney(String(v))} ₸`, n === "revenue" ? "Принёс" : "Оплачено"]} />
                    <Bar dataKey="revenue" fill="var(--ring)" radius={[5, 5, 0, 0]} />
                    <Bar dataKey="paid" fill="var(--success)" radius={[5, 5, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
                <div className="mt-2 flex gap-4 text-xs text-[var(--muted-foreground)]">
                  <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full bg-[var(--ring)]" /> Принёс</span>
                  <span className="flex items-center gap-1.5"><span className="size-2.5 rounded-full bg-[var(--success)]" /> Оплачено</span>
                </div>
              </>
            )}
          </CardContent>
        </Card>
        )}

        {/* статусы */}
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle className="text-base">Заказы по статусам</CardTitle>
          </CardHeader>
          <CardContent>
            {data.by_status.length === 0 ? (
              <p className="py-10 text-center text-sm text-[var(--muted-foreground)]">Заказов нет.</p>
            ) : (
              <div className="flex flex-col items-center gap-4 sm:flex-row">
                <ResponsiveContainer width="100%" height={200} className="max-w-[220px]">
                  <PieChart>
                    <Pie data={data.by_status} dataKey="count" nameKey="label" innerRadius={45} outerRadius={80} paddingAngle={2}>
                      {data.by_status.map((row) => (
                        <Cell key={row.status} fill={STATUS_COLORS[row.status] ?? "#7a7f87"} />
                      ))}
                    </Pie>
                    <Tooltip contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }} />
                  </PieChart>
                </ResponsiveContainer>
                <div className="flex flex-1 flex-col gap-1.5">
                  {data.by_status.map((row) => (
                    <div key={row.status} className="flex items-center justify-between text-sm">
                      <span className="flex items-center gap-2">
                        <span className="size-2.5 rounded-full" style={{ background: STATUS_COLORS[row.status] ?? "#7a7f87" }} />
                        {row.label}
                      </span>
                      <span className="tabular-nums font-medium">{row.count}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* топ товаров — финансовый, под reports.view */}
        {canMoney && (
        <Card>
          <CardHeader><CardTitle className="text-base">Топ товаров</CardTitle></CardHeader>
          <CardContent>
            {data.top_products.length === 0 ? (
              <p className="py-10 text-center text-sm text-[var(--muted-foreground)]">Нет данных.</p>
            ) : (
              <div className="flex flex-col gap-3">
                {data.top_products.map((p) => (
                  <div key={p.product} className="flex flex-col gap-1">
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-medium">{p.label}</span>
                      <span className="tabular-nums">{formatMoney(p.amount)} ₸ · {p.qty} меш.</span>
                    </div>
                    <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--muted)]">
                      <div className="h-full rounded-full bg-[var(--ring)]"
                        style={{ width: `${(Number(p.amount) / maxProductAmount) * 100}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
        )}

        {/* последние заказы */}
        <Card>
          <CardHeader><CardTitle className="text-base">Последние заказы</CardTitle></CardHeader>
          <CardContent>
            {data.recent_orders.length === 0 ? (
              <p className="py-10 text-center text-sm text-[var(--muted-foreground)]">Заказов нет.</p>
            ) : (
              <Table>
                <THead><TR><TH>№</TH><TH>Статус</TH><TH className="text-right">Сумма</TH><TH></TH></TR></THead>
                <TBody>
                  {data.recent_orders.map((o) => (
                    <TR key={o.id}>
                      <TD className="font-medium">#{o.id}</TD>
                      <TD><StatusBadge status={o.status} dot /></TD>
                      <TD className="text-right tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                      <TD>
                        <div className="flex justify-end">
                          <Link href={`/orders/${o.id}`}><Button size="sm" variant="ghost"><ArrowUpRight className="size-4" /></Button></Link>
                        </div>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}

export default function ClientDetailPage(props: { params: Promise<{ id: string }> }) {
  return <RequirePerm perm="clients.view" title="Клиент"><ClientDetailPageInner {...props} /></RequirePerm>;
}
