"use client";

import Link from "next/link";
import { useEffect, useState, type ElementType } from "react";
import {
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDollarSign,
  ClipboardCheck,
  RefreshCw,
  Sparkles,
  Truck,
  Video,
  Wallet,
  Warehouse,
} from "lucide-react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { CameraWall } from "@/components/camera-wall";
import { AppShell } from "@/components/layout/app-shell";
import { StatusBadge } from "@/components/status-badge";
import { ErrorAlert } from "@/components/ui/data-state";
import { formatPlate } from "@/components/ui/license-plate-input";
import { Tabs } from "@/components/ui/tabs";
import { can } from "@/lib/can";
import { useDashboardMetrics, type DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { cn, currencySymbol, formatCompact, formatCompactCurrency, formatMoney } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const TOOLTIP_STYLE = {
  borderRadius: 12,
  border: "1px solid var(--border)",
  background: "var(--card)",
  color: "var(--foreground)",
  fontSize: 12,
  padding: "8px 11px",
  boxShadow: "0 12px 32px rgba(0,0,0,0.12)",
} as const;

type SummaryMetric = {
  key: string;
  label: string;
  value: string;
  exact: string;
  unit: string;
  note: string;
  href: string;
  icon: ElementType;
};

function SummaryHero({ m }: { m: DashboardMetrics }) {
  const metrics: SummaryMetric[] = [
    {
      key: "shipped",
      label: "Отгружено сегодня",
      value: formatCompact(m.shippedToday),
      exact: formatMoney(m.shippedToday),
      unit: "мешков",
      note:
        m.shippedTodayOrders > 0
          ? `${m.shippedTodayOrders} отгрузок · вчера ${formatCompact(m.shippedYesterday)}`
          : `Пока без отгрузок · вчера ${formatCompact(m.shippedYesterday)}`,
      href: "/orders",
      icon: Truck,
    },
    {
      key: "received",
      label: "Поступило сегодня",
      value: formatCompact(String(m.receivedToday)),
      exact: formatMoney(String(m.receivedToday)),
      unit: "₸",
      note: m.receivedTodayCount > 0 ? `${m.receivedTodayCount} подтверждённых оплат` : "Подтверждённых оплат пока нет",
      href: "/accounting",
      icon: Wallet,
    },
    {
      key: "stock",
      label: "Остаток на складе",
      value: formatCompact(m.totalBags),
      exact: formatMoney(m.totalBags),
      unit: "мешков",
      note: `${m.stockByProduct.length} товарных позиций`,
      href: "/warehouse",
      icon: Warehouse,
    },
    {
      key: "debt",
      label: m.overdueClients > 0 ? "Просроченный долг" : "Долг клиентов",
      value: formatCompact(String(m.overdueClients > 0 ? m.overdueTotal : m.debtTotal)),
      exact: formatMoney(String(m.overdueClients > 0 ? m.overdueTotal : m.debtTotal)),
      unit: currencySymbol(m.debtCurrency),
      note: m.overdueClients > 0 ? `${m.overdueClients} клиентов требуют внимания` : "Просроченных оплат нет",
      href: "/accounting",
      icon: CircleDollarSign,
    },
  ].filter((metric) => {
    if (metric.key === "shipped") return m.canOrders && m.canEvents;
    if (metric.key === "received") return m.canFinance && m.canOrders;
    if (metric.key === "stock") return m.canStock;
    return m.canFinance;
  });

  if (metrics.length === 0) return null;

  const [primary, ...secondary] = metrics;
  const attentionCount =
    m.overdueClients + m.attention.pendingPayments + m.attention.awaitingReview + m.negativeStock.length;

  return (
    <section className="analytics-hero relative w-full min-w-0 overflow-hidden rounded-[24px] bg-[var(--hero)] text-[var(--hero-foreground)] shadow-[0_18px_60px_rgba(23,35,28,0.16)]">
      <div aria-hidden="true" className="absolute -right-12 -top-20 size-64 rounded-full border border-white/10" />
      <div aria-hidden="true" className="absolute -right-3 -top-4 size-36 rounded-full border border-white/10" />
      <div className="relative grid min-h-[300px] min-w-0 lg:grid-cols-[1.15fr_0.85fr]">
        <div className="flex min-w-0 flex-col justify-between border-white/10 p-6 sm:p-8 lg:border-r lg:p-10">
          <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.18em] text-white/55">
            <span className="size-2 rounded-full bg-[var(--hero-accent)] shadow-[0_0_0_5px_rgba(217,249,157,0.12)]" />
            Сводка за сегодня
            {attentionCount > 0 && (
              <span className="w-fit rounded-full bg-white/10 px-2.5 py-1 text-center normal-case tracking-normal text-white/75 min-[360px]:ml-2">
                {attentionCount} требуют действия
              </span>
            )}
          </div>

          <div className="py-9 sm:py-12">
            <div className="text-sm text-white/60">{primary.label}</div>
            <div className="mt-2 flex flex-wrap items-end gap-x-3 gap-y-1">
              <span
                title={primary.exact}
                className="text-6xl font-extrabold leading-none tracking-[-0.055em] tabular-nums sm:text-7xl"
              >
                {primary.value}
              </span>
              <span className="mb-1 text-base text-white/55 sm:mb-2">{primary.unit}</span>
            </div>
            <p className="mt-4 text-sm text-white/60">{primary.note}</p>
          </div>

          <Link
            href={primary.href}
            className="group inline-flex min-h-11 w-fit items-center gap-2 text-sm font-semibold text-[var(--hero-accent)] transition hover:text-white"
          >
            Открыть детали
            <ArrowRight className="size-4 transition-transform group-hover:translate-x-1" />
          </Link>
        </div>

        <div className="relative flex min-w-0 flex-col justify-center px-6 pb-6 sm:px-8 sm:pb-8 lg:p-10">
          <div className="divide-y divide-white/10 overflow-hidden rounded-2xl border border-white/10 bg-white/[0.045] backdrop-blur">
            {secondary.map((metric) => {
              const Icon = metric.icon;
              return (
                <Link
                  key={metric.key}
                  href={metric.href}
                  className="group flex items-center gap-4 px-4 py-4 transition hover:bg-white/[0.055]"
                >
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-white/10 text-white/75">
                    <Icon className="size-[18px]" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-white/50">{metric.label}</div>
                    <div className="mt-0.5 flex items-baseline gap-1.5">
                      <span title={metric.exact} className="text-xl font-semibold tracking-tight tabular-nums">
                        {metric.value}
                      </span>
                      <span className="text-xs text-white/45">{metric.unit}</span>
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-white/45">{metric.note}</div>
                  </div>
                  <ChevronRight className="size-4 text-white/25 transition group-hover:translate-x-0.5 group-hover:text-white/70" />
                </Link>
              );
            })}
            {secondary.length === 0 && (
              <div className="flex min-h-44 flex-col items-center justify-center gap-2 px-6 text-center">
                <Sparkles className="size-5 text-[var(--hero-accent)]" />
                <p className="text-sm text-white/55">Главный показатель уже перед вами</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

type AttentionItem = {
  key: string;
  show: boolean;
  href: string;
  label: string;
  hint: string;
  value: string;
  icon: ElementType;
  urgent?: boolean;
};

function AttentionPanel({ m }: { m: DashboardMetrics }) {
  const items: AttentionItem[] = [
    {
      key: "overdue",
      show: m.canFinance && m.overdueClients > 0,
      href: "/accounting",
      label: "Просрочена оплата",
      hint: formatCompactCurrency(m.overdueTotal, m.debtCurrency),
      value: String(m.overdueClients),
      icon: AlertTriangle,
      urgent: true,
    },
    {
      key: "payments",
      show: m.canPayments && m.attention.pendingPayments > 0,
      href: "/accounting",
      label: "Подтвердить оплаты",
      hint: "Ожидают решения кассы",
      value: String(m.attention.pendingPayments),
      icon: Wallet,
    },
    {
      key: "orders",
      show: m.canOrders && m.attention.awaitingReview > 0,
      href: "/orders",
      label: "Рассмотреть заказы",
      hint: "Клиенты ждут ответа",
      value: String(m.attention.awaitingReview),
      icon: ClipboardCheck,
    },
    {
      key: "stock",
      show: m.canStock && m.negativeStock.length > 0,
      href: "/warehouse",
      label: "Исправить остатки",
      hint: "Обнаружен минус на складе",
      value: String(m.negativeStock.length),
      icon: Warehouse,
      urgent: true,
    },
  ].filter((item) => item.show);

  return (
    <section className="flex min-h-[390px] min-w-0 flex-col rounded-[22px] border bg-[var(--card)] shadow-card">
      <div className="flex items-start justify-between gap-4 px-5 pb-4 pt-5">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--muted-foreground)]">
            В фокусе
          </p>
          <h3 className="mt-1 text-lg font-semibold tracking-tight">Нужно решить</h3>
        </div>
        {!m.loading && (
          <span
            className={cn(
              "flex size-9 items-center justify-center rounded-full text-sm font-semibold tabular-nums",
              items.length > 0
                ? "bg-[var(--warning)]/10 text-[var(--warning)]"
                : "bg-[var(--success)]/10 text-[var(--success)]",
            )}
          >
            {items.length}
          </span>
        )}
      </div>

      {m.loading ? (
        <div className="flex flex-1 flex-col gap-3 px-5 py-3">
          {[0, 1, 2].map((row) => (
            <div key={row} className="h-[68px] animate-pulse rounded-2xl bg-[var(--muted)]" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center px-6 pb-10 text-center">
          <span className="flex size-14 items-center justify-center rounded-full bg-[var(--success)]/10 text-[var(--success)]">
            <Check className="size-6" strokeWidth={2.4} />
          </span>
          <h4 className="mt-4 font-semibold">Всё спокойно</h4>
          <p className="mt-1 max-w-[220px] text-sm text-[var(--muted-foreground)]">
            Нет просрочек, неподтверждённых оплат и ошибок склада.
          </p>
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-2 px-3 pb-3">
          {items.map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.key}
                href={item.href}
                className="group flex items-center gap-3 rounded-2xl px-3 py-3 transition hover:bg-[var(--muted)]/70"
              >
                <span
                  className={cn(
                    "flex size-10 shrink-0 items-center justify-center rounded-xl",
                    item.urgent
                      ? "bg-[var(--destructive)]/10 text-[var(--destructive)]"
                      : "bg-[var(--warning)]/10 text-[var(--warning)]",
                  )}
                >
                  <Icon className="size-[18px]" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{item.label}</div>
                  <div className="truncate text-xs text-[var(--muted-foreground)]">{item.hint}</div>
                </div>
                <span className="text-lg font-semibold tabular-nums">{item.value}</span>
                <ArrowUpRight className="size-4 text-[var(--muted-foreground)] opacity-0 transition group-hover:opacity-100" />
              </Link>
            );
          })}
        </div>
      )}
    </section>
  );
}

type TrendMode = "bags" | "money";

function TrendPanel({ m, days }: { m: DashboardMetrics; days: number }) {
  const modes = [
    ...(m.canOrders && m.canEvents ? [{ key: "bags" as const, label: "Отгрузки" }] : []),
    ...(m.canFinance && m.canOrders ? [{ key: "money" as const, label: "Деньги" }] : []),
  ];
  const [mode, setMode] = useState<TrendMode>(modes[0]?.key ?? "bags");
  if (modes.length === 0) return null;

  const active = modes.some((item) => item.key === mode) ? mode : modes[0].key;
  const positiveBags = m.shippedByDay
    .map((day) => day.bags)
    .filter((value) => value > 0)
    .sort((a, b) => b - a);
  const largest = positiveBags[0] ?? 0;
  const secondLargest = positiveBags[1] ?? 0;
  const hasOutlier = secondLargest > 0 && largest > secondLargest * 4;
  const chartCap = hasOutlier ? Math.ceil(secondLargest * 2.5) : Math.max(largest, 1);
  const bagChart = m.shippedByDay.map((day) => ({
    ...day,
    visibleBags: Math.min(day.bags, chartCap),
  }));
  const totalBags = m.shippedByDay.reduce((sum, day) => sum + day.bags, 0);
  const activeDays = positiveBags.length;

  const summary =
    active === "bags"
      ? [
          { label: "За период", value: `${formatCompact(totalBags)} меш.` },
          { label: "Рабочих дней", value: `${activeDays} из ${days}` },
        ]
      : [
          { label: "Выручка", value: `${formatCompact(String(m.periodRevenue))} ₸` },
          { label: "Поступило", value: `${formatCompact(String(m.periodReceived))} ₸` },
        ];

  return (
    <section className="min-h-[390px] min-w-0 rounded-[22px] border bg-[var(--card)] shadow-card">
      <div className="flex flex-wrap items-start justify-between gap-4 px-5 pb-2 pt-5 sm:px-6 sm:pt-6">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--muted-foreground)]">
            Последние {days} дней
          </p>
          <h3 className="mt-1 text-lg font-semibold tracking-tight">Динамика производства</h3>
        </div>
        {modes.length > 1 && (
          <div className="flex rounded-xl bg-[var(--muted)] p-1">
            {modes.map((item) => (
              <button
                key={item.key}
                type="button"
                onClick={() => setMode(item.key)}
                className={cn(
                  "min-h-11 rounded-lg px-3 py-1.5 text-xs font-medium transition sm:min-h-9",
                  active === item.key
                    ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                    : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-x-7 gap-y-2 px-5 py-3 sm:px-6">
        {summary.map((item) => (
          <div key={item.label}>
            <div className="text-[11px] text-[var(--muted-foreground)]">{item.label}</div>
            <div className="mt-0.5 text-sm font-semibold tabular-nums">{item.value}</div>
          </div>
        ))}
      </div>

      <div
        className="h-[235px] w-full px-2 pb-3 sm:px-4"
        role="img"
        aria-label={
          active === "bags"
            ? `График отгрузок за ${days} дней. Всего ${totalBags} мешков.`
            : `График выручки и поступлений за ${days} дней.`
        }
      >
        <ResponsiveContainer width="100%" height="100%">
          {active === "bags" ? (
            <BarChart data={bagChart} margin={{ top: 18, right: 8, left: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 7" vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                interval={days > 14 ? 3 : 1}
                dy={8}
              />
              <Tooltip
                cursor={{ fill: "var(--muted)", opacity: 0.55 }}
                contentStyle={TOOLTIP_STYLE}
                formatter={(_value, _name, item) => [
                  `${formatMoney(Number((item.payload as { bags: number }).bags))} меш.`,
                  "Отгружено",
                ]}
                labelFormatter={(label) => `День ${label}`}
              />
              <Bar dataKey="visibleBags" radius={[6, 6, 2, 2]} maxBarSize={32}>
                {bagChart.map((point) => (
                  <Cell
                    key={point.label}
                    fill={point.bags > chartCap ? "#d6a327" : "var(--ring)"}
                    opacity={point.bags === 0 ? 0.18 : 0.9}
                  />
                ))}
              </Bar>
            </BarChart>
          ) : (
            <AreaChart data={m.spark} margin={{ top: 18, right: 8, left: 8, bottom: 0 }}>
              <defs>
                <linearGradient id="analytics-revenue-fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--ring)" stopOpacity={0.22} />
                  <stop offset="100%" stopColor="var(--ring)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 7" vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                interval={days > 14 ? 3 : 1}
                dy={8}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                formatter={(value: number, name: string) => [
                  `${formatMoney(String(value))} ₸`,
                  name === "revenue" ? "Выручка" : "Поступления",
                ]}
                labelFormatter={(label) => `День ${label}`}
              />
              <Area
                type="monotone"
                dataKey="revenue"
                stroke="var(--ring)"
                strokeWidth={2.25}
                fill="url(#analytics-revenue-fill)"
              />
              <Area type="monotone" dataKey="received" stroke="var(--success)" strokeWidth={2} fillOpacity={0} />
            </AreaChart>
          )}
        </ResponsiveContainer>
      </div>
      <ul className="sr-only">
        {active === "bags"
          ? m.shippedByDay.map((day) => (
              <li key={day.label}>
                День {day.label}: {day.bags} мешков
              </li>
            ))
          : m.spark.map((day) => (
              <li key={day.label}>
                День {day.label}: выручка {day.revenue} ₸, поступления {day.received} ₸
              </li>
            ))}
      </ul>

      {active === "bags" && hasOutlier && (
        <div className="mx-5 mb-4 flex items-center gap-2 rounded-xl bg-[var(--warning)]/10 px-3 py-2 text-xs text-[var(--warning)] sm:mx-6">
          <span className="size-2 shrink-0 rounded-full bg-[#d6a327]" />
          Пик {formatCompact(largest)} меш. выделен. Шкала ограничена, чтобы остальные дни не пропали.
        </div>
      )}
    </section>
  );
}

function LiveQueue({ m }: { m: DashboardMetrics }) {
  if (m.queue.length === 0) return null;

  return (
    <section className="min-w-0 overflow-hidden rounded-[22px] border bg-[var(--card)] shadow-card">
      <div className="flex items-center gap-3 border-b px-5 py-4 sm:px-6">
        <span className="relative flex size-9 items-center justify-center rounded-xl bg-[var(--ring)]/10 text-[var(--ring)]">
          <Truck className="size-[18px]" />
          <span className="absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-[var(--card)] bg-[#62a86a]" />
        </span>
        <div>
          <h3 className="text-sm font-semibold">Сейчас на погрузке</h3>
          <p className="text-xs text-[var(--muted-foreground)]">{m.queue.length} машин в работе</p>
        </div>
        <Link
          href="/shipping"
          className="ml-auto inline-flex items-center gap-1.5 text-xs font-medium text-[var(--ring)] hover:underline"
        >
          Открыть пост <ArrowUpRight className="size-3.5" />
        </Link>
      </div>
      <div className="grid divide-y sm:grid-cols-2 sm:divide-x sm:divide-y-0 xl:grid-cols-4">
        {m.queue.slice(0, 4).map((order) => (
          <Link
            key={order.id}
            href={`/orders/${order.id}`}
            className="group flex items-center gap-3 px-5 py-4 transition hover:bg-[var(--muted)]/60"
          >
            <div className="min-w-0 flex-1">
              <div className="font-semibold tabular-nums">
                {order.truck_number ? formatPlate(order.truck_number) : `Заказ #${order.id}`}
              </div>
              <div className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">
                {order.client_name || "Клиент не указан"}
              </div>
            </div>
            <StatusBadge status={order.status} dot />
            <ChevronRight className="size-4 shrink-0 text-[var(--muted-foreground)] opacity-0 transition group-hover:opacity-100" />
          </Link>
        ))}
      </div>
    </section>
  );
}

const DASHBOARD_VIEWS = [
  { key: "analytics", label: "Аналитика", icon: BarChart3 },
  { key: "cameras", label: "Камеры", icon: Video },
] as const;
type DashboardView = (typeof DASHBOARD_VIEWS)[number]["key"];
const VIEW_STORAGE_KEY = "dashboard:view";

function ViewSwitch({ view, onChange }: { view: DashboardView; onChange: (view: DashboardView) => void }) {
  const tabs = DASHBOARD_VIEWS.map((item) => ({ key: item.key, label: item.label, icon: item.icon }));
  return <Tabs tabs={tabs} active={view} onChange={(key) => onChange(key as DashboardView)} />;
}

const PERIODS = [7, 14, 30] as const;
const PERIOD_STORAGE_KEY = "dashboard:period";

function AnalyticsView() {
  const [days, setDays] = useState(14);
  useEffect(() => {
    const saved = Number(localStorage.getItem(PERIOD_STORAGE_KEY));
    if (PERIODS.includes(saved as (typeof PERIODS)[number])) setDays(saved);
  }, []);

  const changeDays = (value: number) => {
    setDays(value);
    localStorage.setItem(PERIOD_STORAGE_KEY, String(value));
  };
  const m = useDashboardMetrics(days);
  const hasAnyData = m.canOrders || m.canStock || m.canFinance;

  return (
    <div className="mx-auto w-full min-w-0 max-w-[1480px] space-y-5">
      {hasAnyData && (
        <div className="flex flex-wrap items-end justify-between gap-4 pb-1">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[var(--muted-foreground)]">
              Оперативная аналитика
            </p>
            <h2 className="mt-1 text-2xl font-semibold tracking-[-0.025em] sm:text-3xl">Что происходит сегодня</h2>
            <p className="mt-1 text-sm text-[var(--muted-foreground)]">
              Коротко: результат, отклонения и работа в моменте.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="relative">
              <CalendarDays className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <select
                aria-label="Период аналитики"
                value={days}
                onChange={(event) => changeDays(Number(event.target.value))}
                className="h-11 appearance-none rounded-xl border bg-[var(--card)] pl-9 pr-9 text-sm font-medium outline-none transition focus:ring-2 focus:ring-[var(--ring)]/25"
              >
                {PERIODS.map((value) => (
                  <option key={value} value={value}>
                    {value} дней
                  </option>
                ))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
            </div>
            <button
              type="button"
              aria-label="Обновить данные"
              title="Обновить данные"
              onClick={m.reload}
              className="flex size-11 items-center justify-center rounded-xl border bg-[var(--card)] text-[var(--muted-foreground)] transition hover:border-[var(--input)] hover:text-[var(--foreground)]"
            >
              <RefreshCw className="size-4" />
            </button>
          </div>
        </div>
      )}

      {m.loadError && <ErrorAlert message={m.loadError} onRetry={m.reload} />}
      <SummaryHero m={m} />

      {(m.canOrders || m.canFinance || m.canStock) && (
        <div className="grid min-w-0 items-stretch gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <TrendPanel m={m} days={days} />
          <AttentionPanel m={m} />
        </div>
      )}

      {m.canOrders && <LiveQueue m={m} />}

      {!hasAnyData && (
        <section className="flex min-h-72 flex-col items-center justify-center gap-2 rounded-[22px] border border-dashed text-center">
          <BarChart3 className="size-8 text-[var(--muted-foreground)]/40" />
          <p className="font-semibold">Сводка здесь появится, когда роль получит доступы</p>
          <p className="max-w-md text-sm text-[var(--muted-foreground)]">
            Ваши рабочие разделы находятся в меню. Если чего-то не хватает, попросите администратора расширить права
            роли.
          </p>
        </section>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const { me } = useAuth();
  const showCameras = can(me, "shipping.view") || !!me?.is_superuser;
  const [view, setView] = useState<DashboardView | null>(null);

  useEffect(() => {
    const saved = localStorage.getItem(VIEW_STORAGE_KEY);
    setView(saved === "cameras" ? "cameras" : "analytics");
  }, []);

  const changeView = (nextView: DashboardView) => {
    setView(nextView);
    localStorage.setItem(VIEW_STORAGE_KEY, nextView);
  };
  const activeView = showCameras ? view : "analytics";

  return (
    <AppShell
      title="Главная"
      tabs={showCameras && activeView && <ViewSwitch view={activeView} onChange={changeView} />}
    >
      {activeView && (activeView === "analytics" ? <AnalyticsView /> : <CameraWall />)}
    </AppShell>
  );
}
