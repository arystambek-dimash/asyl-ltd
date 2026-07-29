"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ClipboardList,
  Clock3,
  Hourglass,
  Info,
  RefreshCw,
  Truck,
  UserRound,
  Video,
  Wallet,
  Warehouse,
  XCircle,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AppShell } from "@/components/layout/app-shell";
import { CameraWall } from "@/components/camera-wall";
import { ErrorAlert } from "@/components/ui/data-state";
import { Tabs } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/status-badge";
import { formatPlate } from "@/components/ui/license-plate-input";
import { useDashboardMetrics, type DashboardMetrics } from "@/lib/use-dashboard-metrics";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ORDER_STATUS_LABELS } from "@/lib/constants";
import { currencySymbol, formatCompact, formatCompactCurrency, formatCurrency, formatMoney, cn } from "@/lib/utils";

const TOOLTIP_STYLE = {
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--card)",
  fontSize: 12,
  padding: "6px 10px",
  boxShadow: "0 4px 12px rgba(0,0,0,0.06)",
} as const;

const DONUT_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--muted-foreground)",
];

function CardHeader({
  title,
  sub,
  href,
  hrefLabel,
}: {
  title: string;
  sub?: string;
  href?: string;
  hrefLabel?: string;
}) {
  return (
    <div className="flex items-center gap-2.5 border-b px-4 py-3">
      <span className="text-sm font-semibold">{title}</span>
      {sub && <span className="text-xs text-[var(--muted-foreground)]">{sub}</span>}
      {href && (
        <Link
          href={href}
          className="ml-auto flex items-center gap-1 rounded-lg border border-[var(--ring)]/25 bg-[var(--ring)]/5 px-2.5 py-1 text-xs font-medium text-[var(--ring)] transition hover:bg-[var(--ring)]/10"
        >
          {hrefLabel} <ArrowUpRight className="size-3" />
        </Link>
      )}
    </div>
  );
}

/* ── Требует действия ────────────────────────────────────────────── */

/** То, по чему нужно что-то сделать сегодня.
 *
 * Итоговые цифры отвечают на вопрос «как дела», но не говорят, за что
 * браться. Здесь только строки, ведущие на конкретный экран; когда всё
 * закрыто, полоса схлопывается в одну спокойную строку.
 */
function AttentionBar({ m }: { m: DashboardMetrics }) {
  const items = [
    {
      key: "overdue",
      show: m.canFinance && m.overdueClients > 0,
      href: "/accounting",
      icon: AlertTriangle,
      tone: "destructive" as const,
      label: "Просрочена оплата",
      value: String(m.overdueClients),
      hint: `${formatCompactCurrency(m.overdueTotal, m.debtCurrency)} · ${m.overdueClients === 1 ? "клиент" : "клиентов"}`,
    },
    {
      key: "payments",
      show: m.canPayments && m.attention.pendingPayments > 0,
      href: "/accounting",
      icon: Wallet,
      tone: "warning" as const,
      label: "Оплаты на подтверждении",
      value: String(m.attention.pendingPayments),
      hint: "ждут кассу",
    },
    {
      key: "review",
      show: m.canOrders && m.attention.awaitingReview > 0,
      href: "/orders",
      icon: ClipboardList,
      tone: "warning" as const,
      label: "Заказы на рассмотрении",
      value: String(m.attention.awaitingReview),
      hint: "нужен ответ",
    },
    {
      key: "loading",
      show: m.canOrders && m.attention.stuckInLoading > 0,
      href: "/shipping",
      icon: Truck,
      tone: "info" as const,
      label: "Идёт погрузка",
      value: String(m.attention.stuckInLoading),
      hint: "на посту",
    },
  ].filter((item) => item.show);

  // Пока данные не пришли, счётчики нулевые. Зелёное «ничего срочного» на
  // ещё не загруженном экране — обещание, которого система не давала.
  if (m.loading) {
    return (
      <section className="flex items-center gap-2 rounded-xl border bg-[var(--card)] px-4 py-3 text-sm">
        <span className="size-4 shrink-0 animate-pulse rounded-full bg-[var(--muted)]" />
        <span className="text-[var(--muted-foreground)]">Проверяем, что требует внимания…</span>
      </section>
    );
  }

  if (items.length === 0) {
    return (
      <section className="flex items-center gap-2 rounded-xl border border-[var(--success)]/25 bg-[var(--success)]/5 px-4 py-3 text-sm">
        <CheckCircle2 className="size-4 shrink-0 text-[var(--success)]" />
        <span className="text-[var(--muted-foreground)]">Ничего срочного — просрочек и незакрытых оплат нет.</span>
      </section>
    );
  }

  const toneClass = {
    destructive: "border-[var(--destructive)]/25 bg-[var(--destructive)]/5 text-[var(--destructive)]",
    warning: "border-[var(--warning)]/25 bg-[var(--warning)]/5 text-[var(--warning)]",
    info: "border-[var(--ring)]/25 bg-[var(--ring)]/5 text-[var(--ring)]",
  };

  return (
    <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <Link
            key={item.key}
            href={item.href}
            className={cn(
              "group flex items-center gap-2.5 rounded-xl border px-3 py-3 transition hover:shadow-sm",
              toneClass[item.tone],
            )}
          >
            <Icon className="size-5 shrink-0" />
            <div className="min-w-0 flex-1">
              {/* Заголовок и сумма в одну строку каждый: в узкой карточке
                  перенос ломал и подпись, и «2,5 млн ₸» пополам. */}
              <div className="truncate text-[13px] font-medium text-[var(--foreground)]">{item.label}</div>
              <div className="truncate text-xs text-[var(--muted-foreground)]">{item.hint}</div>
            </div>
            {/* Стрелка появляется только при наведении и не занимает места —
                иначе заголовок обрезался уже на «Просрочена оп…». */}
            <div className="shrink-0 whitespace-nowrap text-lg font-semibold tabular-nums">{item.value}</div>
          </Link>
        );
      })}
    </section>
  );
}

/* ── Плитки метрик ───────────────────────────────────────────────── */

function MetricStrip({ m, days }: { m: DashboardMetrics; days: number }) {
  const delta = m.shippedToday - m.shippedYesterday;
  // Крупные суммы показываем порядком величины: «4,81 млрд» читается с
  // одного взгляда, а двенадцать цифр — нет. Точное значение остаётся
  // в подсказке при наведении, ничего не теряется.
  const cells = [
    {
      label: "На складе",
      show: m.canStock,
      value: formatCompact(m.totalBags),
      exact: formatMoney(m.totalBags),
      unit: "меш.",
      hint: "текущий остаток",
      info: "Сумма остатков по всем продуктам склада.",
      icon: Warehouse,
      tone: "bg-blue-500/10 text-blue-600 dark:text-blue-400",
    },
    {
      show: m.canOrders && m.canEvents,
      label: "Ушло сегодня",
      value: formatCompact(m.shippedToday),
      exact: formatMoney(m.shippedToday),
      unit: "меш.",
      hint: `${m.shippedTodayOrders} отгрузок`,
      info: "Мешки в отгруженных сегодня заказах. Стрелка — сравнение со вчера.",
      icon: Truck,
      tone: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
      delta,
    },
    {
      show: m.canFinance && m.canOrders,
      label: `Выручка · ${days} дней`,
      value: formatCompact(String(m.periodRevenue)),
      exact: formatMoney(String(m.periodRevenue)),
      unit: "₸",
      hint: `поступило ${formatCompact(String(m.periodReceived))} ₸`,
      info: "Подтверждённые заказы в тенге за период. Валюты не смешиваются.",
      icon: BarChart3,
      tone: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
    },
    {
      show: m.canFinance,
      label: "Долг клиентов",
      value: formatCompact(String(m.debtTotal)),
      exact: formatMoney(String(m.debtTotal)),
      unit: currencySymbol(m.debtCurrency),
      // Работа в долг здесь норма, поэтому тревожит не сумма, а просрочка.
      hint:
        m.overdueClients > 0
          ? `просрочено ${formatCompactCurrency(m.overdueTotal, m.debtCurrency)} · ${m.overdueClients} кл.`
          : m.topDebtors.length > 0
            ? "просрочки нет"
            : "долгов нет",
      info: "Непогашенный остаток по отгруженным в долг заказам.",
      icon: UserRound,
      tone: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
      alert: m.overdueClients > 0,
    },
  ] as const;
  const visible = cells.filter((c) => c.show);
  if (visible.length === 0) return null;
  return (
    <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {visible.map((c) => {
        const Icon = c.icon;
        return (
          <div
            key={c.label}
            className="relative flex items-start gap-3 rounded-xl border bg-[var(--card)] p-4 shadow-sm"
          >
            <span className={cn("flex size-10 shrink-0 items-center justify-center rounded-xl", c.tone)}>
              <Icon className="size-5" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] text-[var(--muted-foreground)]">{c.label}</div>
              <div className="mt-1 flex flex-wrap items-baseline gap-1.5">
                <span
                  title={`${c.exact}${c.unit ? ` ${c.unit}` : ""}`}
                  className={cn(
                    "text-2xl font-semibold tabular-nums tracking-tight",
                    "alert" in c && c.alert && "text-[var(--destructive)]",
                  )}
                >
                  {c.value}
                </span>
                {c.unit && <span className="text-sm text-[var(--muted-foreground)]">{c.unit}</span>}
                {"delta" in c && c.delta !== 0 && (
                  <span
                    className={cn(
                      "flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[11px] font-medium tabular-nums",
                      c.delta > 0
                        ? "bg-[var(--success)]/10 text-[var(--success)]"
                        : "bg-[var(--destructive)]/10 text-[var(--destructive)]",
                    )}
                  >
                    {c.delta > 0 ? <ArrowUpRight className="size-3" /> : <ArrowDownRight className="size-3" />}
                    {formatCompact(Math.abs(c.delta))}
                  </span>
                )}
              </div>
              <div className="mt-0.5 truncate text-xs text-[var(--muted-foreground)]">{c.hint}</div>
            </div>
            {/* Подсказка «что это за цифра» — по наведению на (i). */}
            <span title={c.info} className="absolute right-3 top-3 cursor-help text-[var(--muted-foreground)]/50">
              <Info className="size-3.5" />
            </span>
          </div>
        );
      })}
    </section>
  );
}

/* ── Отгрузки по дням ────────────────────────────────────────────── */

function ShipmentsCard({ m, days }: { m: DashboardMetrics; days: number }) {
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader
        title="Отгрузки"
        sub={`мешков в день · ${days} дней`}
        href="/shipping"
        hrefLabel="Перейти к отгрузкам"
      />
      <div className="p-4">
        <div className="h-[200px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={m.shippedByDay} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="var(--border)" />
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                interval={days > 14 ? 3 : 1}
              />
              <YAxis
                width={38}
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                tickFormatter={(v: number) => formatCompact(v)}
              />
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

function FinanceCard({ m, days }: { m: DashboardMetrics; days: number }) {
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader title="Финансы" sub={`${days} дней`} href="/reports" hrefLabel="Перейти к финансам" />
      <div className="p-4">
        {/* Крупные «Выручка/Поступило» уже стоят в полосе метрик выше —
            здесь они дублировались и съедали место у графика. */}
        <div className="h-[200px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={m.spark} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="4 4" vertical={false} stroke="var(--border)" />
              <defs>
                <linearGradient id="dash-rev" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--ring)" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="var(--ring)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="label"
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                interval={days > 14 ? 3 : 1}
              />
              <YAxis
                width={44}
                tickLine={false}
                axisLine={false}
                tick={{ fontSize: 11, fill: "var(--muted-foreground)" }}
                tickFormatter={(v: number) => formatCompact(v)}
              />
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
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-[var(--muted-foreground)]">
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-3 rounded bg-[var(--ring)]" /> выручка{" "}
            <b className="font-semibold text-[var(--foreground)]">{formatCompact(String(m.periodRevenue))} ₸</b>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-3 rounded bg-[var(--success)]" /> поступления{" "}
            <b className="font-semibold text-[var(--success)]">{formatCompact(String(m.periodReceived))} ₸</b>
          </span>
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
      <CardHeader title="Склад по продуктам" href="/warehouse" hrefLabel="Перейти к складу" />
      <div className="p-4">
        {total === 0 ? (
          <div className="py-8 text-center text-sm text-[var(--muted-foreground)]">Склад пуст</div>
        ) : (
          <div className="flex items-center gap-4">
            <div className="relative h-[132px] w-[132px] shrink-0">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie
                    data={m.stockByProduct}
                    dataKey="bags"
                    nameKey="name"
                    innerRadius={44}
                    outerRadius={62}
                    paddingAngle={2}
                    strokeWidth={0}
                  >
                    {m.stockByProduct.map((_, i) => (
                      <Cell key={i} fill={DONUT_COLORS[i % DONUT_COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [`${formatMoney(v)} меш.`]} />
                </PieChart>
              </ResponsiveContainer>
              <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                <span title={formatMoney(total)} className="text-lg font-semibold tabular-nums leading-none">
                  {formatCompact(total)}
                </span>
                <span className="mt-0.5 text-[10px] text-[var(--muted-foreground)]">меш.</span>
              </div>
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              {m.stockByProduct.map((p, i) => (
                <div key={p.name} className="flex items-center gap-2 text-xs">
                  <span
                    className="size-2 shrink-0 rounded-[3px]"
                    style={{ background: DONUT_COLORS[i % DONUT_COLORS.length] }}
                  />
                  {/* Полное имя — в подсказке: «ДБН 1с 50кг · Красны…» без неё не прочесть. */}
                  <span title={p.name} className="truncate">
                    {p.name}
                  </span>
                  <span title={formatMoney(p.bags)} className="ml-auto font-medium tabular-nums">
                    {formatCompact(p.bags)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
        {m.negativeStock.length > 0 && (
          <Link
            href="/warehouse"
            className="mt-3 flex items-center gap-2 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning)]/5 px-3 py-2 text-xs text-[var(--warning)] transition hover:bg-[var(--warning)]/10"
          >
            <AlertTriangle className="size-3.5 shrink-0" />
            <span className="min-w-0 truncate">
              Минус на складе: {m.negativeStock.map((row) => `${row.name} ${formatMoney(row.bags)}`).join(", ")}
            </span>
            <ChevronRight className="ml-auto size-3.5 shrink-0" />
          </Link>
        )}
      </div>
    </section>
  );
}

/* ── Заказы по четырём пользовательским статусам ────────────────── */

const PIPELINE_ICONS: Record<string, React.ElementType> = {
  pending: Clock3,
  loading: Hourglass,
  shipped: Truck,
  cancelled: XCircle,
};

function PipelineCard({ m }: { m: DashboardMetrics }) {
  const total = m.pipeline.reduce((s, x) => s + x.count, 0);
  const max = Math.max(...m.pipeline.map((x) => x.count), 1);
  return (
    <section className="flex flex-col rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader title="Заказы по статусам" sub={`${total} всего`} />
      <div className="flex flex-1 flex-col gap-3.5 p-4">
        {m.pipeline.map((row) => {
          const Icon = PIPELINE_ICONS[row.status] ?? Clock3;
          return (
            <div key={row.status} className="flex items-center gap-3">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-[var(--muted)]/70 text-[var(--muted-foreground)]">
                <Icon className="size-4" />
              </span>
              <span className="w-28 shrink-0 truncate text-xs text-[var(--muted-foreground)]">
                {ORDER_STATUS_LABELS[row.status] ?? row.status}
              </span>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[var(--muted)]">
                <div
                  className="h-full rounded-full bg-[var(--ring)] transition-all"
                  style={{ width: `${(row.count / max) * 100}%`, opacity: row.count === 0 ? 0 : 1 }}
                />
              </div>
              <span className="w-8 shrink-0 text-right text-sm font-semibold tabular-nums">{row.count}</span>
            </div>
          );
        })}
      </div>
      <Link
        href="/orders"
        className="flex items-center gap-1 border-t px-4 py-2.5 text-xs font-medium text-[var(--ring)] hover:underline"
      >
        Перейти к заказам <ArrowUpRight className="size-3" />
      </Link>
    </section>
  );
}

/* ── Топ должников ───────────────────────────────────────────────── */

function DebtorsCard({ m }: { m: DashboardMetrics }) {
  return (
    <section className="flex flex-col rounded-xl border bg-[var(--card)] shadow-sm">
      <CardHeader title="Должники" sub="топ-5 по сумме" href="/accounting" hrefLabel="Перейти к должникам" />
      {m.topDebtors.length === 0 ? (
        <div className="flex-1 px-4 py-8 text-center text-sm text-[var(--muted-foreground)]">Долгов нет</div>
      ) : (
        <div className="flex-1 divide-y">
          {m.topDebtors.map((d, i) => (
            <div key={d.client_id} className="flex items-center gap-3 px-4 py-2.5">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-[var(--muted)] text-[11px] font-semibold tabular-nums text-[var(--muted-foreground)]">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{d.client_name}</div>
                <div className="text-[11px] text-[var(--muted-foreground)]">
                  {d.orders_count} зак.
                  {d.overdue_count > 0 && (
                    <span className="text-[var(--destructive)]"> · {d.overdue_count} просрочено</span>
                  )}
                </div>
              </div>
              {/* Валюта у каждого своя: долг в долларах нельзя подписать «₸». */}
              <span
                title={formatCurrency(d.debt_total, d.debt_currency)}
                className="shrink-0 text-sm font-semibold tabular-nums text-[var(--destructive)]"
              >
                {formatCompactCurrency(d.debt_total, d.debt_currency)}
              </span>
            </div>
          ))}
        </div>
      )}
      <Link
        href="/accounting"
        className="flex items-center gap-1 border-t px-4 py-2.5 text-xs font-medium text-[var(--ring)] hover:underline"
      >
        Смотреть всех должников <ArrowUpRight className="size-3" />
      </Link>
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
        <Link
          href="/shipping"
          className="ml-auto flex items-center gap-1 text-xs font-medium text-[var(--ring)] hover:underline"
        >
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
            <Link
              key={o.id}
              href={`/orders/${o.id}`}
              className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-[var(--accent)]"
            >
              <span
                className={cn(
                  "size-2 shrink-0 rounded-full",
                  o.status === "loading" ? "bg-[var(--warning)]" : "bg-[var(--ring)]",
                )}
              />
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
  const tabs = DASHBOARD_VIEWS.map((v) => ({ key: v.key, label: v.label, icon: v.icon }));
  return <Tabs tabs={tabs} active={view} onChange={(k) => onChange(k as DashboardView)} />;
}

const PERIODS = [7, 14, 30] as const;
const PERIOD_STORAGE_KEY = "dashboard:period";

function AnalyticsView() {
  // Период общий для графиков и «Выручки»: 7 — оперативно, 30 — тренд.
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
    <>
      {hasAnyData && (
        <div className="-mb-1 flex items-center justify-end gap-2">
          <div className="relative">
            <CalendarDays className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-[var(--muted-foreground)]" />
            <select
              aria-label="Период аналитики"
              value={days}
              onChange={(event) => changeDays(Number(event.target.value))}
              className="h-8 appearance-none rounded-lg border bg-[var(--card)] pl-8 pr-7 text-[13px] font-medium outline-none transition focus:ring-2 focus:ring-[var(--ring)]/25"
            >
              {PERIODS.map((value) => (
                <option key={value} value={value}>
                  {value} дней
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 top-1/2 size-3.5 -translate-y-1/2 text-[var(--muted-foreground)]" />
          </div>
          <button
            type="button"
            aria-label="Обновить данные"
            title="Обновить данные"
            onClick={m.reload}
            className="flex size-8 items-center justify-center rounded-lg border bg-[var(--card)] text-[var(--muted-foreground)] transition hover:text-[var(--foreground)]"
          >
            <RefreshCw className="size-3.5" />
          </button>
        </div>
      )}
      {m.loadError && <ErrorAlert message={m.loadError} onRetry={m.reload} />}
      {(m.canOrders || m.canFinance || m.canPayments) && <AttentionBar m={m} />}
      <MetricStrip m={m} days={days} />
      {(m.canOrders || m.canFinance) && (
        <div
          className={cn(
            "grid grid-cols-1 items-start gap-4",
            m.canOrders && m.canFinance && m.canEvents && "xl:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)]",
          )}
        >
          {m.canOrders && m.canEvents && <ShipmentsCard m={m} days={days} />}
          {m.canFinance && m.canOrders && <FinanceCard m={m} days={days} />}
        </div>
      )}
      {(m.canStock || m.canOrders || m.canFinance) && (
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2 xl:grid-cols-3">
          {m.canStock && <StockCard m={m} />}
          {m.canOrders && <PipelineCard m={m} />}
          {m.canFinance && <DebtorsCard m={m} />}
        </div>
      )}
      {m.canOrders && <QueueBoard m={m} />}
      {!m.canOrders && !m.canStock && !m.canFinance && (
        // Роль без аналитических прав (например, «Загрузчик»): вместо колонки
        // 403-ошибок — спокойное объяснение и дорога к своим разделам.
        <section className="flex min-h-64 flex-col items-center justify-center gap-2 rounded-xl border border-dashed text-center">
          <BarChart3 className="size-8 text-[var(--muted-foreground)]/40" />
          <p className="font-semibold">Сводка здесь появится, когда роль получит доступы</p>
          <p className="max-w-md text-sm text-[var(--muted-foreground)]">
            Ваши рабочие разделы — в меню слева. Если чего-то не хватает, попросите администратора расширить права роли.
          </p>
        </section>
      )}
    </>
  );
}

export default function DashboardPage() {
  const { me } = useAuth();
  // Камеры открываются по shipping.view — без него вкладка вела бы в 403.
  const showCameras = can(me, "shipping.view") || !!me?.is_superuser;
  // null до чтения localStorage: иначе AnalyticsView успевает смонтироваться
  // и выстрелить своими запросами даже у тех, кто живёт на камерах.
  // (Читать в инициализаторе useState нельзя — SSR-разметка разойдётся с клиентом.)
  const [view, setView] = useState<DashboardView | null>(null);
  useEffect(() => {
    const saved = localStorage.getItem(VIEW_STORAGE_KEY);
    setView(saved === "cameras" ? "cameras" : "analytics");
  }, []);
  const changeView = (v: DashboardView) => {
    setView(v);
    localStorage.setItem(VIEW_STORAGE_KEY, v);
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
