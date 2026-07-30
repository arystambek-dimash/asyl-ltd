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
import { useDebounced } from "@/lib/use-debounced";
import { useLocalDay } from "@/lib/use-local-day";
import { translateOrderStatusMessage } from "@/lib/constants";
import {
  Search,
  X,
  CircleDot,
  Wallet,
  PackageCheck,
  Truck,
  Forklift,
  Warehouse,
  ArrowDownToLine,
  Scale,
  Activity,
} from "lucide-react";
import type { EventLog, EventLogPage } from "@/lib/types";

type EventMeta = {
  label: string;
  icon: ComponentType<{ className?: string }>;
  /** базовый цвет события как CSS-переменная темы */
  color: string;
};

const EVENT_META: Record<string, EventMeta> = {
  status: { label: "Статус", icon: CircleDot, color: "var(--ring)" },
  status_override: { label: "Статус", icon: CircleDot, color: "var(--ring)" },
  status_request: { label: "Запрос статуса", icon: CircleDot, color: "var(--warning)" },
  payment: { label: "Оплата", icon: Wallet, color: "var(--success)" },
  receipt: { label: "Приёмка", icon: PackageCheck, color: "var(--ring)" },
  arrival: { label: "Прибытие", icon: Truck, color: "var(--ring)" },
  loading: { label: "Загрузка", icon: Forklift, color: "var(--warning)" },
  shipment: { label: "Отгрузка", icon: ArrowDownToLine, color: "var(--ring)" },
  shipment_rollback: { label: "Откат отгрузки", icon: ArrowDownToLine, color: "var(--destructive)" },
  debt_override: { label: "Долг", icon: Scale, color: "var(--destructive)" },
  stock_adjust: { label: "Склад", icon: Warehouse, color: "var(--warning)" },
};

const FALLBACK_META: EventMeta = { label: "Событие", icon: Activity, color: "var(--muted-foreground)" };
const EVENT_DAY_FORMATTER = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  year: "numeric",
});
const EVENT_TIME_FORMATTER = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit",
});
const EVENTS_PER_PAGE = 100;

function metaFor(eventType: string): EventMeta {
  return EVENT_META[eventType] ?? { ...FALLBACK_META, label: eventType };
}

function dateGroupLabel(d: Date, currentDay: string): string {
  const today = new Date(`${currentDay}T12:00:00`);
  const startOf = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diffDays = Math.round((startOf(today) - startOf(d)) / 86_400_000);
  if (diffDays === 0) return "Сегодня";
  if (diffDays === 1) return "Вчера";
  return EVENT_DAY_FORMATTER.format(d);
}

function EventsPageInner() {
  const currentDay = useLocalDay();
  const [type, setType] = useState("");
  const [order, setOrder] = useState("");
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [page, setPage] = useState(1);
  // Свободный ввод не должен дёргать API на каждую букву.
  const debouncedSearch = useDebounced(search);
  const debouncedOrder = useDebounced(order);

  const url = useMemo(() => {
    const q = new URLSearchParams();
    q.set("page", String(page));
    q.set("page_size", String(EVENTS_PER_PAGE));
    if (type) q.set("event_type", type);
    if (debouncedOrder) q.set("order", debouncedOrder);
    if (debouncedSearch) q.set("search", debouncedSearch);
    if (dateFrom) q.set("date_from", dateFrom);
    if (dateTo) q.set("date_to", dateTo);
    return `/events/?${q.toString()}`;
  }, [page, type, debouncedOrder, debouncedSearch, dateFrom, dateTo]);

  const { data: eventPage, loading, error, reload } = useApi<EventLogPage>(url);
  const events = eventPage?.results;

  // Группируем события по календарному дню (сохраняя порядок ленты).
  const groups = useMemo(() => {
    const out: { key: string; label: string; items: EventLog[] }[] = [];
    for (const e of events ?? []) {
      const d = new Date(e.created_at);
      const key = d.toDateString();
      let g = out[out.length - 1];
      if (!g || g.key !== key) {
        g = { key, label: dateGroupLabel(d, currentDay), items: [] };
        out.push(g);
      }
      g.items.push(e);
    }
    return out;
  }, [currentDay, events]);

  const hasFilters = Boolean(type || order || search || dateFrom || dateTo);
  const totalPages = Math.max(1, Math.ceil((eventPage?.count ?? 0) / EVENTS_PER_PAGE));
  const firstVisible = (page - 1) * EVENTS_PER_PAGE + 1;
  const lastVisible = Math.min(page * EVENTS_PER_PAGE, eventPage?.count ?? 0);

  function reset() {
    setType("");
    setOrder("");
    setSearch("");
    setDateFrom("");
    setDateTo("");
    setPage(1);
  }

  return (
    <AppShell
      title="Журнал событий"
      section="Управление"
      description="Неизменяемая лента событий системы: оплаты, отгрузки, движения склада и статусы заказов."
    >
      <Card className="mb-4">
        <CardContent className="pt-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="event-type">Тип события</Label>
              <Select
                id="event-type"
                value={type}
                onChange={(e) => {
                  setType(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">Все типы</option>
                {Object.entries(EVENT_META).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v.label}
                  </option>
                ))}
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="event-order">№ заказа</Label>
              <Input
                id="event-order"
                type="number"
                placeholder="напр. 12"
                value={order}
                onChange={(e) => {
                  setOrder(e.target.value);
                  setPage(1);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="event-date-from">Дата с</Label>
              <Input
                id="event-date-from"
                type="date"
                value={dateFrom}
                onChange={(e) => {
                  setDateFrom(e.target.value);
                  setPage(1);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="event-date-to">Дата по</Label>
              <Input
                id="event-date-to"
                type="date"
                value={dateTo}
                onChange={(e) => {
                  setDateTo(e.target.value);
                  setPage(1);
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="event-search">Поиск</Label>
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
                <Input
                  id="event-search"
                  className="pl-8"
                  placeholder="по сообщению"
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setPage(1);
                  }}
                />
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
          ) : error && !eventPage ? (
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
                    <span className="text-xs text-[var(--muted-foreground)]">{g.items.length} соб.</span>
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
                            <p className="text-sm font-medium text-[var(--foreground)]">
                              {translateOrderStatusMessage(e.message, e.payload)}
                            </p>
                          </div>
                          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                            {EVENT_TIME_FORMATTER.format(new Date(e.created_at))}
                            {e.order ? ` · заказ #${e.order}` : ""}
                            {e.user_name ? ` · ${e.user_name}` : ""}
                          </p>
                        </li>
                      );
                    })}
                  </ol>
                </div>
              ))}
            </div>
          )}
          {eventPage && eventPage.count > 0 && (
            <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--border)] pt-4">
              <p className="text-xs text-[var(--muted-foreground)]" aria-live="polite">
                События {firstVisible}–{lastVisible} из {eventPage.count} · страница {page} из {totalPages}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!eventPage.previous}
                  onClick={() => setPage((value) => Math.max(1, value - 1))}
                >
                  Назад
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!eventPage.next}
                  onClick={() => setPage((value) => value + 1)}
                >
                  Далее
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}

export default function EventsPage() {
  return (
    <RequirePerm perm="events.view" title="Журнал">
      <EventsPageInner />
    </RequirePerm>
  );
}
