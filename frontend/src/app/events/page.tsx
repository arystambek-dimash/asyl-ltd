"use client";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { useApi } from "@/lib/use-api";
import { Search, X } from "lucide-react";
import type { EventLog } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  status: "Статус", payment: "Оплата", receipt: "Приёмка",
  arrival: "Прибытие", loading: "Загрузка", shipment: "Отгрузка",
  debt_override: "Долг", stock_adjust: "Склад",
};

export default function EventsPage() {
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

  const { data: events, loading } = useApi<EventLog[]>(url);

  const hasFilters = type || order || search || dateFrom || dateTo;
  function reset() {
    setType(""); setOrder(""); setSearch(""); setDateFrom(""); setDateTo("");
  }

  return (
    <AppShell title="Журнал событий">
      <Card className="mb-4">
        <CardContent className="pt-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <div className="flex flex-col gap-1.5">
              <Label>Тип события</Label>
              <Select value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">Все типы</option>
                {Object.entries(TYPE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
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
          ) : (events ?? []).length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
              {hasFilters ? "Ничего не найдено по фильтрам." : "Событий пока нет."}
            </p>
          ) : (
            <ul className="flex flex-col">
              {(events ?? []).map((e) => (
                <li key={e.id} className="flex items-start gap-3 border-b py-3 last:border-0">
                  <Badge tone="muted" className="mt-0.5 shrink-0">
                    {TYPE_LABELS[e.event_type] ?? e.event_type}
                  </Badge>
                  <div className="flex-1">
                    <p className="text-sm">{e.message}</p>
                    <p className="text-xs text-[var(--muted-foreground)]">
                      {new Date(e.created_at).toLocaleString("ru-RU")}
                      {e.order ? ` · заказ #${e.order}` : ""}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
