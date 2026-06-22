"use client";
import { useMemo } from "react";
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid,
  RadialBarChart, RadialBar, PolarAngleAxis,
} from "recharts";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { formatMoney } from "@/lib/utils";
import { TrendingUp, Package, Truck, Wallet, AlertCircle } from "lucide-react";
import type { Order, StockItem, EventLog } from "@/lib/types";

const ARC_COLORS = ["#2563eb", "#f97316", "#16a34a", "#9333ea", "#0891b2", "#dc2626"];

function fmtDay(iso: string) {
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

/* — верхние KPI-карты — */
function MetricCard({ label, value, sub, icon: Icon }: {
  label: string; value: string; sub: string; icon: React.ElementType;
}) {
  return (
    <Card className="rounded-2xl p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
          {label}
        </div>
        <div className="flex size-8 items-center justify-center rounded-lg bg-[var(--secondary)]">
          <Icon className="size-4 text-[var(--muted-foreground)]" />
        </div>
      </div>
      <div className="mt-3 text-3xl font-bold tabular-nums tracking-tight">{value}</div>
      <div className="mt-1 text-xs text-[var(--muted-foreground)]">{sub}</div>
    </Card>
  );
}

/* — нижние градиентные карты — */
const GRADIENTS: Record<string, string> = {
  green: "from-emerald-500 to-emerald-700",
  blue: "from-blue-500 to-blue-700",
  purple: "from-violet-500 to-violet-700",
  orange: "from-orange-500 to-orange-600",
};
function GradientCard({ tone, label, value, sub }: {
  tone: keyof typeof GRADIENTS; label: string; value: string; sub: string;
}) {
  return (
    <div className={`relative overflow-hidden rounded-2xl bg-gradient-to-br ${GRADIENTS[tone]} p-5 text-white shadow-md`}>
      <svg className="absolute inset-0 h-full w-full opacity-20" preserveAspectRatio="none" viewBox="0 0 400 200">
        <path d="M0 140 Q100 90 200 130 T400 110 V200 H0 Z" fill="white" fillOpacity="0.25" />
        <path d="M0 160 Q120 120 240 155 T400 140 V200 H0 Z" fill="white" fillOpacity="0.15" />
      </svg>
      <div className="relative">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em] opacity-80">{label}</div>
        <div className="mt-3 text-2xl font-bold tabular-nums tracking-tight">{value}</div>
        <div className="mt-1 text-xs opacity-80">{sub}</div>
      </div>
    </div>
  );
}

export default function ReportsPage() {
  const { data: orders } = useApi<Order[]>("/orders/");
  const { data: stock } = useApi<StockItem[]>("/stock/");
  const { data: events } = useApi<EventLog[]>("/events/");

  const list = orders ?? [];
  const revenue = list.reduce((s, o) => s + Number(o.paid_total), 0);
  const totalOrderSum = list.reduce((s, o) => s + Number(o.total_amount), 0);
  const shipped = list.filter((o) => o.status === "shipped").length;
  const active = list.filter((o) => !["shipped", "cancelled"].includes(o.status)).length;
  const totalBags = (stock ?? []).reduce((s, i) => s + i.bags, 0);
  const debtors = list.filter((o) => !o.is_fully_paid && o.status !== "cancelled");
  const debt = debtors.reduce((s, o) => s + (Number(o.total_amount) - Number(o.paid_total)), 0);

  // радиальный: остатки по сортам
  const radial = useMemo(() => {
    const by: Record<string, number> = {};
    (stock ?? []).forEach((s) => { by[s.grade] = (by[s.grade] || 0) + s.bags; });
    const entries = Object.entries(by).filter(([, v]) => v > 0);
    return entries.map(([name, value], i) => ({
      name, value, fill: ARC_COLORS[i % ARC_COLORS.length],
    }));
  }, [stock]);
  const radialMax = Math.max(1, ...radial.map((r) => r.value));

  // сплайн: оплаты и отгрузки по дням (из журнала)
  const series = useMemo(() => {
    const byDay: Record<string, { day: string; payments: number; shipments: number }> = {};
    (events ?? []).forEach((e) => {
      if (e.event_type !== "payment" && e.event_type !== "shipment") return;
      const day = fmtDay(e.created_at);
      byDay[day] = byDay[day] || { day, payments: 0, shipments: 0 };
      if (e.event_type === "payment") {
        const amt = Number((e.payload?.amount as string) ?? 0);
        byDay[day].payments += amt;
      } else {
        byDay[day].shipments += 1;
      }
    });
    return Object.values(byDay).reverse();
  }, [events]);

  return (
    <AppShell title="Отчёты">
      {/* KPI */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Поступило оплат" value={`${formatMoney(revenue)} ₸`} sub="всего по заказам" icon={Wallet} />
        <MetricCard label="Отгружено" value={String(shipped)} sub="завершённых заказов" icon={Truck} />
        <MetricCard label="Остаток" value={formatMoney(totalBags)} sub="мешков на складе" icon={Package} />
        <MetricCard label="Активные" value={String(active)} sub="заказов в работе" icon={TrendingUp} />
      </div>

      {/* Графики */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[380px_1fr]">
        {/* радиальный — остатки по сортам */}
        <Card className="rounded-2xl">
          <CardHeader><CardTitle>Остатки по сортам</CardTitle></CardHeader>
          <CardContent>
            {radial.length === 0 ? (
              <p className="py-16 text-center text-sm text-[var(--muted-foreground)]">Нет данных по складу.</p>
            ) : (
              <>
                <ResponsiveContainer width="100%" height={240}>
                  <RadialBarChart innerRadius="30%" outerRadius="100%" data={radial}
                    startAngle={90} endAngle={-270}>
                    <PolarAngleAxis type="number" domain={[0, radialMax]} tick={false} />
                    <RadialBar background dataKey="value" cornerRadius={8} />
                  </RadialBarChart>
                </ResponsiveContainer>
                <div className="mt-2 flex flex-col gap-1.5">
                  {radial.map((r) => (
                    <div key={r.name} className="flex items-center justify-between text-sm">
                      <span className="flex items-center gap-2">
                        <span className="size-2.5 rounded-full" style={{ background: r.fill }} />
                        {r.name}
                      </span>
                      <span className="tabular-nums font-medium">{formatMoney(r.value)} меш.</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        {/* сплайн — оплаты/отгрузки по дням */}
        <Card className="rounded-2xl">
          <CardHeader><CardTitle>Динамика оплат и отгрузок</CardTitle></CardHeader>
          <CardContent>
            {series.length === 0 ? (
              <p className="py-16 text-center text-sm text-[var(--muted-foreground)]">Пока нет операций для графика.</p>
            ) : (
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={series} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="pay" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#2563eb" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#2563eb" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="ship" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#f97316" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#f97316" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                  <XAxis dataKey="day" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="l" tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false}
                    width={48} tickFormatter={(v) => (v >= 1000 ? `${v / 1000}k` : String(v))} />
                  <Tooltip
                    contentStyle={{ borderRadius: 12, border: "1px solid var(--border)", fontSize: 12 }}
                    formatter={(v: number, n) => n === "payments" ? [`${formatMoney(v)} ₸`, "Оплаты"] : [v, "Отгрузки"]} />
                  <Area yAxisId="l" type="monotone" dataKey="payments" stroke="#2563eb" strokeWidth={2.5} fill="url(#pay)" />
                  <Area yAxisId="l" type="monotone" dataKey="shipments" stroke="#f97316" strokeWidth={2.5} fill="url(#ship)" />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Градиентные карты */}
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <GradientCard tone="green" label="Поступило оплат" value={`${formatMoney(revenue)} ₸`} sub="оплачено клиентами" />
        <GradientCard tone="blue" label="Сумма заказов" value={`${formatMoney(totalOrderSum)} ₸`} sub="всего оформлено" />
        <GradientCard tone="purple" label="Остаток на складе" value={`${formatMoney(totalBags)}`} sub="мешков готовой муки" />
        <GradientCard tone="orange" label="Дебиторка" value={`${formatMoney(debt)} ₸`} sub="неоплаченный остаток" />
      </div>

      {/* Дебиторка */}
      <Card className="mt-6 rounded-2xl">
        <CardHeader className="flex-row items-center gap-2">
          <AlertCircle className="size-4 text-[var(--muted-foreground)]" />
          <CardTitle>Дебиторская задолженность</CardTitle>
        </CardHeader>
        <CardContent>
          {debtors.length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Долгов нет.</p>
          ) : (
            <Table>
              <THead><TR><TH>Заказ</TH><TH>Клиент</TH><TH>Сумма</TH><TH>Оплачено</TH><TH>Остаток</TH></TR></THead>
              <TBody>
                {debtors.map((o) => (
                  <TR key={o.id}>
                    <TD className="font-medium">#{o.id}</TD>
                    <TD>{o.client_name || "—"}</TD>
                    <TD className="tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums">{formatMoney(o.paid_total)} ₸</TD>
                    <TD className="tabular-nums font-medium text-[var(--destructive)]">
                      {formatMoney(Number(o.total_amount) - Number(o.paid_total))} ₸
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
