"use client";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useApi } from "@/lib/use-api";
import type { EventLog } from "@/lib/types";

const TYPE_LABELS: Record<string, string> = {
  status: "Статус", payment: "Оплата", receipt: "Приёмка",
  arrival: "Прибытие", loading: "Загрузка", shipment: "Отгрузка",
  debt_override: "Долг", stock_adjust: "Склад",
};

export default function EventsPage() {
  const { data: events, loading } = useApi<EventLog[]>("/events/");
  return (
    <AppShell title="Журнал событий">
      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : (events ?? []).length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Событий пока нет.</p>
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
