"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ArrowDownRight, ArrowUpRight, BarChart3, ChevronRight, Truck, Video,
} from "lucide-react";
import {
  Area, AreaChart, Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis,
} from "recharts";
import { AppShell } from "@/components/layout/app-shell";
import { CameraWall } from "@/components/camera-wall";
import { ErrorAlert } from "@/components/ui/data-state";
import { StatusBadge } from "@/components/status-badge";
import { formatPlate } from "@/components/ui/license-plate-input";
import { useDashboardMetrics, type DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { ORDER_STATUS_LABELS } from "@/lib/constants";
import { formatMoney, cn } from "@/lib/utils";

const TOOLTIP_STYLE = {
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--card)",
  fontSize: 12,
  padding: "6px 10px",
  boxShadow: "0 4px 12px rgba(0,0,0,0.06)",
} as const;

const DONUT_COLORS = [
  "var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--muted-foreground)",
];

function CardHeader({ title, sub, href, hrefLabel }: {
  title: string; sub?: string; href?: string; hrefLabel?: string;
}) {
  return (
    <div className="flex items-center gap-2.5 border-b px-4 py-3">
      <span className="text-sm font-semibold">{title}</span>
      {sub && <span className="text-xs text-[var(--muted-foreground)]">{sub}</span>}
      {href && (
        <Link href={href} className="ml-auto flex items-center gap-1 text-xs font-medium text-[var(--ring)] hover:underline">
          {hrefLabel} <ArrowUpRight className="size-3" />
        </Link>
      )}
    </div>
  );
}

/* ── Полоса метрик ───────────────────────────────────────────────── */

function MetricStrip({ m }: { m: DashboardMetrics }) {
  const delta = m.shippedToday - m.shippedYesterday;
  const cells = [
    { label: "На складе", value: formatMoney(m.totalBags), unit: "меш.", hint: "текущий остаток" },
    {
      label: "Ушло сегодня", value: formatMoney(m.shippedToday), unit: "меш.",
      hint: `${m.shippedTodayOrders} отгрузок`, delta,
    },
    { label: "Выручка · 14 дней", value: formatMoney(String(m.periodRevenue)), unit: "₸", hint: `поступило ${formatMoney(String(m.periodReceived))} ₸` },
    { label: "Долг клиентов", value: formatMoney(String(m.debtTotal)), unit: "₸", hint: `${m.topDebtors.length > 0 ? "по подтверждённым заказам" : "долгов нет"}`, alert: m.debtTotal > 0 },
  ] as const;
  return (
    <section className="grid grid-cols-2 divide-y rounded-xl border bg-[var(--card)] shadow-sm sm:divide-x sm:divide-y-0 xl:grid-cols-4 max-sm:divide-x-0">
      {cells.map((c) => (
        <div key={c.label} className="px-5 py-4">
          <div className="text-[13px] text-[var(--muted-foreground)]">{c.label}</div>
          <div className="mt-1.5 flex items-baseline gap-1.5">
            <span className={cn(
              "text-2xl font-semibold tabular-nums tracking-tight",
              "alert" in c && c.alert && "text-[var(--destructive)]",
            )}>
              {c.value}
            </span>
            {c.unit && <span className="text-sm text-[var(--muted-foreground)]">{c.unit}</span>}
            {"delta" in c && c.delta !== 0 && (
              <span className={cn(
                "flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium tabular-nums",
                c.delta > 0 ? "bg-[var(--success)]/10 text-[var(--success)]" : "bg-[var(--destructive)]/10 text-[var(--destructive)]",
              )}>
                {c.delta > 0 ? <ArrowUpRight className="size-3" /> : <ArrowDownRight className="size-3" />}
                {formatMoney(Math.abs(c.delta))}
              </span>
            )}
          </div>
          <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{c.hint}</div>
        </div>
      ))}
    </section>
  );
}

/* ── Отгрузки по дням (14д) ──────────────────────────────────────── */

function ShipmentsCard({ m }: { m: DashboardMetrics }) {
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader title="Отгрузки" sub="мешков в день · 14 дней" href="/shipping" hrefLabel="Пост отгрузки" />
      <div className="p-4">
        <div className="h-[180px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={m.shippedByDay} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
              <XAxis dataKey="label" tickLine={false} axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} interval={1} />
              <Tooltip
                cursor={{ fill: "var(--muted)" }}
                contentStyle={TOOLTIP_STYLE}
                formatter={(v: number) => [`${formatMoney(v)} меш.`, "Отгружено"]}
                labelFormatter={(l) => `День ${l}`}
              />
              <Bar dataKey="bags" fill="var(--ring)" radius={[3, 3, 0, 0]} maxBarSize={28} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  );
}

/* ── Финансы 14д ─────────────────────────────────────────────────── */

function FinanceCard({ m }: { m: DashboardMetrics }) {
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader title="Финансы" sub="14 дней" href="/reports" hrefLabel="Отчёты" />
      <div className="p-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <div className="text-[13px] text-[var(--muted-foreground)]">Выручка</div>
            <div className="mt-1 text-xl font-semibold tabular-nums tracking-tight">
              {formatMoney(String(m.periodRevenue))} ₸
            </div>
          </div>
          <div>
            <div className="text-[13px] text-[var(--muted-foreground)]">Поступило</div>
            <div className="mt-1 text-xl font-semibold tabular-nums tracking-tight text-[var(--success)]">
              {formatMoney(String(m.periodReceived))} ₸
            </div>
          </div>
        </div>
        <div className="mt-4 h-[120px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={m.spark} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="dash-rev" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--ring)" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="var(--ring)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="label" tickLine={false} axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }} interval={1} />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                formatter={(v: number, name: string) => [
                  `${formatMoney(String(v))} ₸`,
                  name === "revenue" ? "Выручка" : "Поступления",
                ]}
                labelFormatter={(l) => `День ${l}`}
              />
              <Area type="monotone" dataKey="revenue" stroke="var(--ring)" strokeWidth={1.75} fill="url(#dash-rev)" />
              <Area type="monotone" dataKey="received" stroke="var(--success)" strokeWidth={1.5} fillOpacity={0} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-2 flex items-center gap-4 text-xs text-[var(--muted-foreground)]">
          <span className="flex items-center gap-1.5"><span className="h-0.5 w-3 rounded bg-[var(--ring)]" /> выручка</span>
          <span className="flex items-center gap-1.5"><span className="h-0.5 w-3 rounded bg-[var(--success)]" /> поступления</span>
        </div>
      </div>
    </section>
  );
}

/* ── Склад по продуктам ──────────────────────────────────────────── */

function StockCard({ m }: { m: DashboardMetrics }) {
  const total = m.stockByProduct.reduce((s, x) => s + x.bags, 0);
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader title="Склад" sub="по продуктам" href="/warehouse" hrefLabel="Склад" />
      <div className="p-4">
        {total === 0 ? (
          <div className="py-8 text-center text-sm text-[var(--muted-foreground)]">Склад пуст</div>
        ) : (
          <div className="flex items-center gap-4">
            <div className="relative h-[132px] w-[132px] shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={m.stockByProduct} dataKey="bags" nameKey="name"
                    innerRadius={44} outerRadius={62} paddingAngle={2} strokeWidth={0}>
                    {m.stockByProduct.map((_, i) => (
                      <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${formatMoney(v)} меш.`]} />
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-lg font-semibold tabular-nums leading-none">{formatMoney(total)}</span>
                <span className="mt-0.5 text-[10px] text-[var(--muted-foreground)]">меш.</span>
              </div>
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              {m.stockByProduct.map((p, i) => (
                <div key={p.name} className="flex items-center gap-2 text-xs">
                  <span className="size-2 shrink-0 rounded-[3px]" style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }} />
                  <span className="truncate">{p.name}</span>
                  <span className="ml-auto font-medium tabular-nums">{formatMoney(p.bags)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

/* ── Заказы в работе по статусам ─────────────────────────────────── */

function PipelineCard({ m }: { m: DashboardMetrics }) {
  const total = m.pipeline.reduce((s, x) => s + x.count, 0);
  const max = Math.max(...m.pipeline.map((x) => x.count), 1);
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader title="Заказы в работе" sub={`${total} активных`} href="/orders" hrefLabel="Заказы" />
      <div className="flex flex-col gap-3 p-4">
        {m.pipeline.map((row) => (
          <div key={row.status} className="flex items-center gap-3">
            <span className="w-32 shrink-0 truncate text-xs text-[var(--muted-foreground)]">
              {ORDER_STATUS_LABELS[row.status] ?? row.status}
            </span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--muted)]">
              <div className="h-full rounded-full bg-[var(--ring)] transition-all"
                style={{ width: `${(row.count / max) * 100}%`, opacity: row.count === 0 ? 0 : 1 }} />
            </div>
            <span className="w-6 shrink-0 text-right text-sm font-semibold tabular-nums">{row.count}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── Топ должников ───────────────────────────────────────────────── */

function DebtorsCard({ m }: { m: DashboardMetrics }) {
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader title="Должники" sub="топ-5 по сумме" href="/debts" hrefLabel="Долги" />
      {m.topDebtors.length === 0 ? (
        <div className="px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">Долгов нет</div>
      ) : (
        <div className="divide-y">
          {m.topDebtors.map((d) => (
            <div key={d.client_id} className="flex items-center gap-3 px-4 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{d.client_name}</div>
                <div className="text-[11px] text-[var(--muted-foreground)]">
                  {d.orders_count} зак.{d.overdue_count > 0 && (
                    <span className="text-[var(--destructive)]"> · {d.overdue_count} просрочено</span>
                  )}
                </div>
              </div>
              <span className="text-sm font-semibold tabular-nums text-[var(--destructive)]">
                {formatMoney(d.debt_total)} ₸
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Очередь отгрузки ────────────────────────────────────────────── */

function QueueBoard({ m }: { m: DashboardMetrics }) {
  const queue = m.queue;
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <div className="flex items-center gap-2.5 border-b px-4 py-3">
        <Truck className="size-4 text-[var(--muted-foreground)]" />
        <span className="text-sm font-semibold">Очередь отгрузки</span>
        <span className="text-xs text-[var(--muted-foreground)]">{queue.length} в работе</span>
        <Link href="/shipping" className="ml-auto flex items-center gap-1 text-xs font-medium text-[var(--ring)] hover:underline">
          Пост отгрузки <ArrowUpRight className="size-3" />
        </Link>
      </div>
      {queue.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-1.5 px-4 py-10 text-center">
          <div className="text-sm font-medium">Нет машин в работе</div>
          <div className="text-xs text-[var(--muted-foreground)]">Машины появятся здесь после въезда на весы</div>
        </div>
      ) : (
        <div className="divide-y">
          {queue.map((o) => (
            <Link key={o.id} href={`/orders/${o.id}`}
              className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-[var(--accent)]">
              <span className={cn(
                "size-2 shrink-0 rounded-full",
                o.status === "loading" ? "bg-[var(--warning)]" :
                o.status === "loaded" ? "bg-[var(--success)]" : "bg-[var(--ring)]",
              )} />
              <span className="w-28 shrink-0 text-sm font-semibold tabular-nums">
                {o.truck_number ? formatPlate(o.truck_number) : `#${o.id}`}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{o.client_name || "—"}</div>
              </div>
              <span className="text-xs tabular-nums text-[var(--muted-foreground)]">#{o.id}</span>
              <StatusBadge status={o.status} dot />
              <ChevronRight className="size-4 text-[var(--muted-foreground)] opacity-0 transition-opacity group-hover:opacity-100" />
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Переключатель разделов ──────────────────────────────────────── */

const DASHBOARD_VIEWS = [
  { key: "analytics", label: "Аналитика", icon: BarChart3 },
  { key: "cameras", label: "Камеры", icon: Video },
] as const;
type DashboardView = (typeof DASHBOARD_VIEWS)[number]["key"];
const VIEW_STORAGE_KEY = "dashboard:view";

function ViewSwitch({ view, onChange }: { view: DashboardView; onChange: (v: DashboardView) => void }) {
  return (
    <div className="inline-flex rounded-md border border-[var(--border)] bg-[var(--muted)] p-0.5">
      {DASHBOARD_VIEWS.map((v) => (
        <button key={v.key} type="button" onClick={() => onChange(v.key)}
          className={cn(
            "inline-flex h-7 items-center gap-1.5 rounded px-3 text-[13px] transition-colors",
            view === v.key
              ? "bg-[var(--card)] font-medium text-[var(--foreground)] shadow-sm"
              : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
          )}>
          <v.icon className="size-3.5" />
          {v.label}
        </button>
      ))}
    </div>
  );
}

function AnalyticsView() {
  const m = useDashboardMetrics();
  return (
    <>
      {m.loadError && <ErrorAlert message={m.loadError} onRetry={m.reload} />}
      <MetricStrip m={m} />
      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)]">
        <ShipmentsCard m={m} />
        <FinanceCard m={m} />
      </div>
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2 xl:grid-cols-3">
        <StockCard m={m} />
        <PipelineCard m={m} />
        <DebtorsCard m={m} />
      </div>
      <QueueBoard m={m} />
    </>
  );
}

export default function DashboardPage() {
  const [view, setView] = useState<DashboardView>("analytics");

  // запоминаем выбранный раздел между визитами
  useEffect(() => {
    const saved = localStorage.getItem(VIEW_STORAGE_KEY);
    if (saved === "cameras" || saved === "analytics") setView(saved);
  }, []);
  const changeView = (v: DashboardView) => {
    setView(v);
    localStorage.setItem(VIEW_STORAGE_KEY, v);
  };

  return (
    <AppShell title="Главная">
      <div className="flex flex-col gap-4">
        <ViewSwitch view={view} onChange={changeView} />
        {view === "analytics" ? <AnalyticsView /> : <CameraWall />}
      </div>
    </AppShell>
  );
}
