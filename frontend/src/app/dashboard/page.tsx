"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Activity, ArrowUpRight, Boxes, ChevronRight, Factory, PackageCheck, Truck, Warehouse,
} from "lucide-react";
import { ResponsiveContainer, AreaChart, Area } from "recharts";
import { AppShell } from "@/components/layout/app-shell";
import { CameraWall } from "@/components/camera-wall";
import { StatusBadge } from "@/components/status-badge";
import { formatPlate } from "@/components/ui/license-plate-input";
import { useDashboardMetrics } from "@/lib/use-dashboard-metrics";
import { formatMoney, cn } from "@/lib/utils";

/* ── Живые часы деки (Алматы) ────────────────────────────────────── */

function DeckClock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const time = now
    ? new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "Asia/Almaty" }).format(now)
    : "--:--:--";
  const [hh, mm, ss] = time.split(":");
  const date = now
    ? new Intl.DateTimeFormat("ru-RU", { weekday: "long", day: "numeric", month: "long", timeZone: "Asia/Almaty" }).format(now)
    : "";

  return (
    <div className="flex flex-col items-end">
      <div className="font-[family-name:var(--font-mono)] text-3xl font-bold tabular-nums leading-none text-white sm:text-4xl">
        {hh}<span className="cmd-blink text-[color:var(--warning)]">:</span>{mm}
        <span className="text-lg text-white/40 sm:text-xl">:{ss}</span>
      </div>
      <div className="mt-1 font-[family-name:var(--font-mono)] text-[10px] uppercase tracking-[0.28em] text-white/40">
        {date || " "} · ALMT
      </div>
    </div>
  );
}

/* ── Тёмная дека с камерами ──────────────────────────────────────── */

function CommandDeck() {
  return (
    <section
      className="cmd-noise cmd-rise relative overflow-hidden rounded-2xl border border-black/40 p-4 shadow-[0_24px_60px_-24px_rgba(0,0,0,0.55)] sm:p-5"
      style={{ background: "linear-gradient(160deg, #1b1d21 0%, #131417 55%, #17150f 100%)" }}
    >
      {/* Пшеничная кромка сверху */}
      <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-[color:var(--warning)]/80 to-transparent" />

      <div className="relative mb-4 flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[color:var(--warning)]">
            <Factory className="size-4" />
            <span className="font-[family-name:var(--font-mono)] text-[10px] font-semibold uppercase tracking-[0.3em]">
              Асыл-LTD · Мельничный комплекс
            </span>
          </div>
          <h2 className="mt-2 font-[family-name:var(--font-display)] text-xl font-bold uppercase leading-none tracking-wide text-white sm:text-2xl">
            Пульт цеха
          </h2>
        </div>
        <DeckClock />
      </div>

      <div className="relative">
        <CameraWall />
      </div>
    </section>
  );
}

/* ── Крупные цифры дня ───────────────────────────────────────────── */

function KpiRow() {
  const { totalBags, shippedToday, shippedTotal, shippedTodayOrders } = useDashboardMetrics();
  const kpis = [
    { icon: Warehouse, label: "На складе", value: formatMoney(totalBags), unit: "меш.", hint: "текущий остаток" },
    { icon: ArrowUpRight, label: "Ушло сегодня", value: formatMoney(shippedToday), unit: "меш.", hint: "отгружено за сегодня", accent: true },
    { icon: Boxes, label: "Отгружено всего", value: formatMoney(shippedTotal), unit: "меш.", hint: "за всё время" },
    { icon: PackageCheck, label: "Заказов сегодня", value: String(shippedTodayOrders), unit: "зак.", hint: "отгрузок за сегодня" },
  ] as const;
  return (
    <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      {kpis.map((k, i) => (
        <div
          key={k.label}
          className={cn(
            "cmd-rise group relative overflow-hidden rounded-xl border bg-[var(--card)] p-4 shadow-sm transition-shadow hover:shadow-md",
            "accent" in k && k.accent && "border-[color:var(--warning)]/40",
          )}
          style={{ animationDelay: `${0.1 + i * 0.07}s` }}
        >
          {"accent" in k && k.accent && (
            <div className="absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r from-[color:var(--warning)] to-transparent" />
          )}
          <div className="flex items-center gap-2 text-[var(--muted-foreground)]">
            <k.icon className={cn("size-3.5", "accent" in k && k.accent && "text-[color:var(--warning)]")} />
            <span className="font-[family-name:var(--font-mono)] text-[10px] font-semibold uppercase tracking-[0.16em]">
              {k.label}
            </span>
          </div>
          <div className="mt-2.5 flex items-baseline gap-1.5">
            <span className={cn(
              "font-[family-name:var(--font-display)] text-[26px] font-bold leading-none tabular-nums tracking-tight sm:text-3xl",
              "accent" in k && k.accent && "text-[color:var(--warning)]",
            )}>
              {k.value}
            </span>
            <span className="text-xs font-medium text-[var(--muted-foreground)]">{k.unit}</span>
          </div>
          <div className="mt-1.5 text-[11px] text-[var(--muted-foreground)]">{k.hint}</div>
        </div>
      ))}
    </section>
  );
}

/* ── Табло очереди отгрузки ──────────────────────────────────────── */

function QueueBoard() {
  const { queue } = useDashboardMetrics();
  return (
    <section className="cmd-rise flex flex-col rounded-xl border bg-[var(--card)] shadow-sm" style={{ animationDelay: "0.35s" }}>
      <div className="flex items-center gap-2.5 border-b px-4 py-3">
        <Truck className="size-4 text-[var(--muted-foreground)]" />
        <span className="text-sm font-semibold">Очередь отгрузки</span>
        <span className={cn(
          "ml-auto rounded-full px-2 py-0.5 font-[family-name:var(--font-mono)] text-[11px] font-bold tabular-nums",
          queue.length > 0 ? "bg-[color:var(--warning)]/15 text-[color:var(--warning)]" : "bg-[var(--muted)] text-[var(--muted-foreground)]",
        )}>
          {queue.length} в работе
        </span>
      </div>

      {queue.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-4 py-12">
          <div className="flex size-12 items-center justify-center rounded-full border border-dashed border-[var(--input)]">
            <Truck className="size-5 text-[var(--muted-foreground)]" />
          </div>
          <div className="text-center">
            <div className="text-sm font-medium">Нет машин в работе</div>
            <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
              Машины появятся здесь после въезда на весы
            </div>
          </div>
        </div>
      ) : (
        <div className="divide-y">
          {queue.map((o, i) => (
            <Link
              key={o.id}
              href={`/orders/${o.id}`}
              className="cmd-rise group flex items-center gap-3.5 px-4 py-3 transition-colors hover:bg-[var(--accent)]"
              style={{ animationDelay: `${0.4 + i * 0.05}s` }}
            >
              <span className="font-[family-name:var(--font-mono)] text-xs font-bold tabular-nums text-[var(--muted-foreground)]">
                {String(i + 1).padStart(2, "0")}
              </span>
              <div className="rounded-md border border-[var(--input)] bg-[var(--secondary)] px-2.5 py-1 font-[family-name:var(--font-mono)] text-sm font-bold tabular-nums tracking-wider">
                {o.truck_number ? formatPlate(o.truck_number) : `#${o.id}`}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium">{o.client_name || "—"}</div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Заказ #{o.id}</div>
              </div>
              <StatusBadge status={o.status} dot />
              <ChevronRight className="size-4 text-[var(--muted-foreground)] transition-transform group-hover:translate-x-0.5" />
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Аналитика: выручка и поступления 14д ────────────────────────── */

function AnalyticsCard() {
  const { spark, periodRevenue, periodReceived } = useDashboardMetrics();
  return (
    <Link
      href="/reports"
      className="cmd-rise group flex flex-col rounded-xl border bg-[var(--card)] p-4 shadow-sm transition-all hover:border-[color:var(--warning)]/50 hover:shadow-md"
      style={{ animationDelay: "0.42s" }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="flex size-9 items-center justify-center rounded-lg bg-[color:var(--warning)]/12">
            <Activity className="size-4 text-[color:var(--warning)]" />
          </div>
          <div>
            <div className="text-sm font-semibold">Аналитика</div>
            <div className="text-[11px] text-[var(--muted-foreground)]">Выручка, поступления, динамика</div>
          </div>
        </div>
        <ChevronRight className="size-4 text-[var(--muted-foreground)] transition-transform group-hover:translate-x-0.5" />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3">
        <div>
          <div className="font-[family-name:var(--font-mono)] text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
            Выручка 14д
          </div>
          <div className="mt-1 font-[family-name:var(--font-display)] text-lg font-bold leading-none tabular-nums">
            {formatMoney(String(periodRevenue))} ₸
          </div>
        </div>
        <div>
          <div className="font-[family-name:var(--font-mono)] text-[10px] font-semibold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
            Поступило 14д
          </div>
          <div className="mt-1 font-[family-name:var(--font-display)] text-lg font-bold leading-none tabular-nums text-[var(--success)]">
            {formatMoney(String(periodReceived))} ₸
          </div>
        </div>
      </div>

      <div className="mt-3 h-[56px] w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={spark} margin={{ top: 2, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="spark-rev" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--warning)" stopOpacity={0.35} />
                <stop offset="100%" stopColor="var(--warning)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area type="monotone" dataKey="revenue" stroke="var(--warning)" strokeWidth={2} fill="url(#spark-rev)" />
            <Area type="monotone" dataKey="received" stroke="var(--success)" strokeWidth={1.5} fillOpacity={0} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </Link>
  );
}

export default function DashboardPage() {
  return (
    <AppShell title="Командный центр">
      <div className="flex flex-col gap-4">
        <CommandDeck />
        <KpiRow />
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[1fr_360px]">
          <QueueBoard />
          <AnalyticsCard />
        </div>
      </div>
    </AppShell>
  );
}
