"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, BarChart3, ChevronRight, Truck, Video } from "lucide-react";
import { ResponsiveContainer, AreaChart, Area } from "recharts";
import { AppShell } from "@/components/layout/app-shell";
import { CameraWall } from "@/components/camera-wall";
import { StatusBadge } from "@/components/status-badge";
import { formatPlate } from "@/components/ui/license-plate-input";
import { useDashboardMetrics } from "@/lib/use-dashboard-metrics";
import { formatMoney, cn } from "@/lib/utils";

/* Полоса метрик — как у Stripe: маленький лейбл, крупное число, пояснение. */
function MetricStrip() {
  const { totalBags, shippedToday, shippedTotal, shippedTodayOrders } = useDashboardMetrics();
  const metrics = [
    { label: "На складе", value: formatMoney(totalBags), unit: "меш.", hint: "текущий остаток" },
    { label: "Ушло сегодня", value: formatMoney(shippedToday), unit: "меш.", hint: "отгружено за сегодня" },
    { label: "Отгружено всего", value: formatMoney(shippedTotal), unit: "меш.", hint: "за всё время" },
    { label: "Заказов сегодня", value: String(shippedTodayOrders), unit: "", hint: "отгрузок за сегодня" },
  ];
  return (
    <section className="grid grid-cols-2 divide-y rounded-xl border bg-[var(--card)] shadow-sm sm:divide-x sm:divide-y-0 xl:grid-cols-4 max-sm:divide-x-0">
      {metrics.map((m) => (
        <div key={m.label} className="px-5 py-4">
          <div className="text-[13px] text-[var(--muted-foreground)]">{m.label}</div>
          <div className="mt-1.5 flex items-baseline gap-1">
            <span className="text-2xl font-semibold tabular-nums tracking-tight">{m.value}</span>
            {m.unit && <span className="text-sm text-[var(--muted-foreground)]">{m.unit}</span>}
          </div>
          <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{m.hint}</div>
        </div>
      ))}
    </section>
  );
}

/* Очередь отгрузки — статусборд в духе Vercel: строка на машину, статус справа. */
function QueueBoard() {
  const { queue } = useDashboardMetrics();
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
        <div className="flex flex-col items-center justify-center gap-1.5 px-4 py-14 text-center">
          <div className="text-sm font-medium">Нет машин в работе</div>
          <div className="text-xs text-[var(--muted-foreground)]">
            Машины появятся здесь после въезда на весы
          </div>
        </div>
      ) : (
        <div className="divide-y">
          {queue.map((o) => (
            <Link
              key={o.id}
              href={`/orders/${o.id}`}
              className="group flex items-center gap-3 px-4 py-3 transition-colors hover:bg-[var(--accent)]"
            >
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

/* Финансы за 14 дней — две цифры и график, ссылка в отчёты. */
function FinanceCard() {
  const { spark, periodRevenue, periodReceived } = useDashboardMetrics();
  return (
    <section className="rounded-xl border bg-[var(--card)] shadow-sm">
      <div className="flex items-center border-b px-4 py-3">
        <span className="text-sm font-semibold">Финансы · 14 дней</span>
        <Link href="/reports" className="ml-auto flex items-center gap-1 text-xs font-medium text-[var(--ring)] hover:underline">
          Отчёты <ArrowUpRight className="size-3" />
        </Link>
      </div>
      <div className="p-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <div className="text-[13px] text-[var(--muted-foreground)]">Выручка</div>
            <div className="mt-1 text-xl font-semibold tabular-nums tracking-tight">
              {formatMoney(String(periodRevenue))} ₸
            </div>
          </div>
          <div>
            <div className="text-[13px] text-[var(--muted-foreground)]">Поступило</div>
            <div className="mt-1 text-xl font-semibold tabular-nums tracking-tight text-[var(--success)]">
              {formatMoney(String(periodReceived))} ₸
            </div>
          </div>
        </div>
        <div className="mt-4 h-[72px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={spark} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="spark-rev" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--ring)" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="var(--ring)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area type="monotone" dataKey="revenue" stroke="var(--ring)" strokeWidth={1.75} fill="url(#spark-rev)" />
              <Area type="monotone" dataKey="received" stroke="var(--success)" strokeWidth={1.5} fillOpacity={0} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-2 flex items-center gap-4 text-xs text-[var(--muted-foreground)]">
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-3 rounded bg-[var(--ring)]" /> выручка
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-0.5 w-3 rounded bg-[var(--success)]" /> поступления
          </span>
        </div>
      </div>
    </section>
  );
}

/* Переключатель разделов дашборда — в стиле FilterPills. */
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
        <button
          key={v.key}
          type="button"
          onClick={() => onChange(v.key)}
          className={cn(
            "inline-flex h-7 items-center gap-1.5 rounded px-3 text-[13px] transition-colors",
            view === v.key
              ? "bg-[var(--card)] font-medium text-[var(--foreground)] shadow-sm"
              : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
          )}
        >
          <v.icon className="size-3.5" />
          {v.label}
        </button>
      ))}
    </div>
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
    <AppShell title="Командный центр">
      <div className="flex flex-col gap-4">
        <ViewSwitch view={view} onChange={changeView} />

        {view === "analytics" ? (
          <>
            <MetricStrip />
            <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(320px,1fr)]">
              <QueueBoard />
              <FinanceCard />
            </div>
          </>
        ) : (
          <CameraWall />
        )}
      </div>
    </AppShell>
  );
}
