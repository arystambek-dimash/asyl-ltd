"use client";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SearchInput } from "@/components/ui/search-input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { usePagedApi } from "@/lib/use-paged-api";
import { useDebounced } from "@/lib/use-debounced";
import { groupByDay } from "@/lib/day-groups";
import { useLocalDay } from "@/lib/use-local-day";
import { translateOrderStatusMessage } from "@/lib/constants";
import { formatTime } from "@/lib/utils";
import { EVENT_TYPE_GROUPS, eventTypeMeta } from "@/lib/event-types";
import { X } from "lucide-react";
import type { EventLog } from "@/lib/types";

const EVENTS_PER_PAGE = 100;

function EventsPageInner() {
  const currentDay = useLocalDay();
  const [type, setType] = useState("");
  const [order, setOrder] = useState("");
  const [search, setSearch] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  // Свободный ввод не должен дёргать API на каждую букву.
  const debouncedSearch = useDebounced(search);
  const debouncedOrder = useDebounced(order);

  // Смена фильтров меняет URL — хук сам начинает ленту с первой страницы.
  const url = useMemo(() => {
    const q = new URLSearchParams();
    if (type) q.set("event_type", type);
    if (debouncedOrder) q.set("order", debouncedOrder);
    if (debouncedSearch) q.set("search", debouncedSearch);
    if (dateFrom) q.set("date_from", dateFrom);
    if (dateTo) q.set("date_to", dateTo);
    const qs = q.toString();
    return `/events/${qs ? `?${qs}` : ""}`;
  }, [type, debouncedOrder, debouncedSearch, dateFrom, dateTo]);

  const {
    items: events,
    count,
    hasMore,
    loading,
    loadingMore,
    error,
    reload,
    loadMore,
  } = usePagedApi<EventLog>(url, EVENTS_PER_PAGE);

  // Группируем события по календарному дню (сохраняя порядок ленты).
  const groups = useMemo(() => groupByDay(events, (e) => new Date(e.created_at), currentDay), [currentDay, events]);

  const hasFilters = Boolean(type || order || search || dateFrom || dateTo);

  function reset() {
    setType("");
    setOrder("");
    setSearch("");
    setDateFrom("");
    setDateTo("");
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
                }}
              >
                <option value="">Все типы</option>
                {EVENT_TYPE_GROUPS.map((group) => (
                  <optgroup key={group.label} label={group.label}>
                    {Object.entries(group.types).map(([k, v]) => (
                      <option key={k} value={k}>
                        {v.label}
                      </option>
                    ))}
                  </optgroup>
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
                }}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="event-search">Поиск</Label>
              <SearchInput
                id="event-search"
                placeholder="по сообщению"
                value={search}
                onChange={(e) => {
                  setSearch(e.target.value);
                }}
              />
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
          ) : error && events.length === 0 ? (
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
                      const m = eventTypeMeta(e.event_type);
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
                              {translateOrderStatusMessage(e.message)}
                            </p>
                          </div>
                          <p className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                            {formatTime(e.created_at)}
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
          {/* Ошибка доклейки не должна быть немой: лента уже на экране,
              поэтому пустое состояние сверху её не покажет. */}
          {error && events.length > 0 && <ErrorAlert message={error} onRetry={loadMore} />}
          <LoadMore shown={events.length} total={count} hasMore={hasMore} loading={loadingMore} onClick={loadMore} />
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
