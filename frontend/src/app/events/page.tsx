"use client";
import { useMemo, useState, type ComponentType } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ErrorAlert } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import {
  Search, X, CircleDot, Wallet, PackageCheck, Truck,
  Forklift, Warehouse, ArrowDownToLine, Scale, Activity,
} from "lucide-react";
import type { EventLog } from "@/lib/types";

type EventMeta = {
  label: string;
  icon: ComponentType<{ className?: string }>;
  /** базовый цвет события как CSS-переменная темы */
  color: string;
};

const EVENT_META: Record<string, EventMeta> = {
  status:        { label: "Статус",   icon: CircleDot,       color: "var(--ring)" },
  payment:       { label: "Оплата",   icon: Wallet,          color: "var(--success)" },
  receipt:       { label: "Приёмка",  icon: PackageCheck,    color: "var(--ring)" },
  arrival:       { label: "Прибытие", icon: Truck,           color: "var(--ring)" },
  loading:       { label: "Загрузка", icon: Forklift,        color: "var(--warning)" },
  shipment:      { label: "Отгрузка", icon: ArrowDownToLine, color: "var(--ring)" },
  debt_override: { label: "Долг",     icon: Scale,           color: "var(--destructive)" },
  stock_adjust:  { label: "Склад",    icon: Warehouse,       color: "var(--warning)" },
};

const FALLBACK_META: EventMeta = { label: "Событие", icon: Activity, color: "var(--muted-foreground)" };

function metaFor(eventType: string): EventMeta {
  return EVENT_META[eventType] ?? { ...FALLBACK_META, label: eventType };
}

function dateGroupLabel(d: Date): string {
  const today = new Date();
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((startOf(today) - startOf(d)) / 86_400_000);
  if (diffDays === 0) return "Сегодня";
  if (diffDays === 1) return "Вчера";
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

function EventsPageInner() {
  const [type, setType] = useState("");
  const [order, setOrder] = useState("");
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const url = useMemo(() => {
    const q = new URLSearchParams();
    if (type) q.set("event_type", type);
    if (order) q.set("order", order);
    if (search) q.set("search", search);
    if (dateFrom) q.set("date_from", dateFrom);
    if (dateTo) q.set("date_to", dateTo);
    const s = q.toString();
    return s ? `/events/?${s}` : "/events/";
  }, [type, order, search, dateFrom, dateTo]);

  const { data: events, loading, error, reload } = useApi<EventLog[]>(url);

  // Группируем события по календарному дню (сохраняя порядок ленты).
  const groups = useMemo(() => {
    const out: { key: string; label: string; items: EventLog[] }[] = [];
    for (const e of events ?? []) {
      const d = new Date(e.created_at);
      const key = d.toDateString();
      let g = out[out.length - 1];
      if (!g || g.key !== key) {
        g = { key, label: dateGroupLabel(d), items: [] };
        out.push(g);
      }
      g.items.push(e);
    }
    return out;
  }, [events]);

  const hasFilters = type || order || search || dateFrom || dateTo;
  function reset() {
    setType(""); setOrder(""); setSearch(""); setDateFrom(""); setDateTo("");
  }

  return (
    <AppShell title="Журнал событий" section="Управление" description="Неизменяемая лента событий системы: оплаты, отгрузки, движения склада и статусы заказов.">
      <Card className="mb-4">
        <CardContent className="pt-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <div className="flex flex-col gap-1.5">
              <Label>Тип события</Label>
              <Select value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">Все типы</option>
                {Object.entries(EVENT_META).map(([k, v]) => (
                  <option key={k} value={k}>{v.label}</option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>№ заказа</Label>
              <Input type="number" placeholder="напр. 12" value={order}
                onChange={(e) => setOrder(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Дата с</Label>
              <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Дата по</Label>
              <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Поиск</Label>
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
                <Input className="pl-8" placeholder="по сообщению" value={search}
                  onChange={(e) => setSearch(e.target.value)} />
              </div>
            </div>
          </div>
          {hasFilters && (
            <div className="mt-3 flex justify-end">
              <Button variant="ghost" size="sm" onClick={reset}>
                <X className="size-4" /> Сбросить фильтры
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : error && !events ? (
            <ErrorAlert message={error} onRetry={reload} />
          ) : groups.length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
              {hasFilters ? "Ничего не найдено по фильтрам." : "Событий пока нет."}
            </p>
          ) : (
            <div className="flex flex-col gap-6">
              {groups.map((g) => (
                <div key={g.key}>
                  <div className="mb-2 flex items-center gap-3">
                    <span className="text-xs font-semibold uppercase tracking-wide text-[var(--muted-foreground)]">
                      {g.label}
                    </span>
                    <span className="h-px flex-1 bg-[var(--border)]" />
                    <span className="text-xs text-[var(--muted-foreground)]">
                      {g.items.length} соб.
                    </span>
                  </div>
                  <ol className="relative ml-3 border-l border-[var(--border)]">
                    {g.items.map((e) => {
                      const m = metaFor(e.event_type);
                      const Icon = m.icon;
                      return (
                        <li key={e.id} className="relative pb-4 pl-6 last:pb-0">
                          {/* кружок-иконка на линии */}
                          <span
                            className="absolute -left-[13px] top-0 flex size-[26px] items-center justify-center rounded-full ring-4 ring-[var(--card)]"
                            style={{ background: `color-mix(in oklab, ${m.color} 14%, transparent)`, color: m.color }}
                          >
                            <Icon className="size-3.5" />
                          </span>
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                            <span
                              className="rounded-md px-1.5 py-0.5 text-[11px] font-medium leading-none"
                              style={{ background: `color-mix(in oklab, ${m.color} 12%, transparent)`, color: m.color }}
                            >
                              {m.label}
                            </span>
                            <p className="text-sm font-medium text-[var(--foreground)]">{e.message}</p>
                          </div>
                          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                            {new Date(e.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}
                            {e.order ? ` · заказ #${e.order}` : ""}
                          </p>
                        </li>
                      );
                    })}
                  </ol>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}

export default function EventsPage() {
  return <RequirePerm perm="events.view" title="Журнал"><EventsPageInner /></RequirePerm>;
}
