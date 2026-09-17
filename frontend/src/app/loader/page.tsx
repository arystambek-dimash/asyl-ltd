"use client";
import { useState } from "react";
import { CheckCircle2, PackageCheck, Printer, Search, Settings2, TrainFront, Truck } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { WaybillSettingsModal } from "@/components/loader/waybill-settings-modal";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { DataGate } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { LoadMore } from "@/components/ui/load-more";
import { Tabs } from "@/components/ui/tabs";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { loaderUrl, openWaybill, shiftIsoDate, type LoaderOrder } from "@/lib/loader";
import { useDebounced } from "@/lib/use-debounced";
import { usePagedApi } from "@/lib/use-paged-api";
import { cn, formatCurrency, formatTime, pluralRu, todayLocalIsoDate } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type View = "queue" | "history";

const bagsLabel = (count: number) => `${count} ${pluralRu(count, ["мешок", "мешка", "мешков"])}`;

function dayLabel(iso: string, today: string) {
  if (iso === today) return "сегодня";
  if (iso === shiftIsoDate(today, 1)) return "завтра";
  if (iso === shiftIsoDate(today, -1)) return "вчера";
  const [, month, day] = iso.split("-");
  return `${day}.${month}`;
}

function TransportLabel({ order }: { order: LoaderOrder }) {
  const Icon = order.transport_type === "train" ? TrainFront : Truck;
  const text =
    order.transport_type === "train"
      ? `Вагон${order.truck_number ? ` ${order.truck_number}` : ""}`
      : order.truck_number || "номер машины не указан";
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5">
      <Icon className="size-4 shrink-0" />
      <span className={cn("truncate", !order.truck_number && order.transport_type === "truck" && "italic")}>
        {text}
      </span>
    </span>
  );
}

function amountLabel(order: LoaderOrder) {
  return Number(order.total_amount) > 0 ? formatCurrency(order.total_amount, order.currency) : "цена не закреплена";
}

function LoaderPageInner() {
  const { me } = useAuth();
  const canConfirm = can(me, "loader.confirm");
  const canEditSettings = can(me, "sys_permissions.manage");
  const today = todayLocalIsoDate();
  const [view, setView] = useState<View>("queue");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search.trim());
  const [queueDay, setQueueDay] = useState("");
  const [range, setRange] = useState({ from: today, to: today });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [truckNumber, setTruckNumber] = useState("");
  const [shipped, setShipped] = useState<LoaderOrder | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);

  const queue = usePagedApi<LoaderOrder>(loaderUrl("queue", { day: queueDay, search: debouncedSearch }));
  const history = usePagedApi<LoaderOrder>(
    view === "history"
      ? loaderUrl("history", { date_from: range.from, date_to: range.to, search: debouncedSearch })
      : null,
  );
  const selected = queue.items.find((order) => order.id === selectedId) ?? null;
  const needsTruckNumber = selected?.transport_type === "truck" && !selected.truck_number;

  function select(order: LoaderOrder) {
    setShipped(null);
    setError("");
    setTruckNumber("");
    setSelectedId((current) => (current === order.id ? null : order.id));
  }

  async function confirm() {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<LoaderOrder>(`/loader/orders/${selected.id}/dispatch/`, {
        truck_number: needsTruckNumber ? truckNumber : "",
      });
      setShipped(data);
      setSelectedId(null);
      setTruckNumber("");
      void queue.reload();
      if (view === "history") void history.reload();
    } catch (cause) {
      setError(apiError(cause));
      // Заказ мог уехать с другого устройства — покажем актуальную очередь.
      void queue.reload();
    } finally {
      setBusy(false);
    }
  }

  async function print(orderId: number) {
    setError("");
    try {
      await openWaybill(orderId);
    } catch (cause) {
      setError(apiError(cause));
    }
  }

  const footer = (
    <div className="border-t bg-[var(--card)] px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-8">
      {error && (
        <p
          role="alert"
          className="mb-2 rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
        >
          {error}
        </p>
      )}
      {shipped ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <CheckCircle2 className="size-7 shrink-0 text-[var(--success)]" />
            <div className="min-w-0">
              <div className="font-semibold">Заказ №{shipped.id} отгружен</div>
              <div className="truncate text-sm text-[var(--muted-foreground)]">
                {shipped.client_name} · {bagsLabel(shipped.bags)}
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" className="h-14 flex-1 sm:flex-none" onClick={() => setShipped(null)}>
              Готово
            </Button>
            <Button className="h-14 flex-[2] text-base sm:flex-none sm:px-8" onClick={() => void print(shipped.id)}>
              <Printer className="size-5" /> Печать накладной
            </Button>
          </div>
        </div>
      ) : view === "queue" && canConfirm ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1 text-sm">
            {selected ? (
              <>
                <div className="font-semibold">
                  №{selected.id} · {selected.client_name}
                </div>
                <div className="text-[var(--muted-foreground)]">
                  {bagsLabel(selected.bags)} · {Number(selected.total_kg)} кг · {amountLabel(selected)}
                </div>
              </>
            ) : (
              <div className="text-[var(--muted-foreground)]">Выберите заказ в списке</div>
            )}
          </div>
          {needsTruckNumber && (
            <Input
              aria-label="Номер машины"
              placeholder="Номер машины, например 403 BJN 13"
              autoCapitalize="characters"
              value={truckNumber}
              onChange={(event) => setTruckNumber(event.target.value.toUpperCase())}
              className="h-12 text-base sm:w-64 sm:text-sm"
            />
          )}
          <Button className="h-14 text-base sm:min-w-72" disabled={!selected || busy} onClick={confirm}>
            <PackageCheck className="size-5" />
            {busy ? "Отгружаем…" : selected ? `Подтвердить отгрузку №${selected.id}` : "Подтвердить отгрузку"}
          </Button>
        </div>
      ) : null}
    </div>
  );

  const showFooter = Boolean(shipped || error || (view === "queue" && canConfirm));
  return (
    <AppShell
      title="Грузчик"
      section="Работа"
      tabs={
        <Tabs
          active={view}
          onChange={(key) => {
            setView(key as View);
            setSelectedId(null);
          }}
          tabs={[
            { key: "queue", label: "К отгрузке", count: queue.count },
            { key: "history", label: "История" },
          ]}
        />
      }
      actions={
        canEditSettings && (
          <Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)} aria-label="Настройки накладной">
            <Settings2 className="size-4" /> <span className="hidden sm:inline">Накладная</span>
          </Button>
        )
      }
      footer={showFooter ? footer : undefined}
    >
      <div className="mb-4 flex flex-col gap-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            aria-label="Поиск"
            className="h-11 pl-9 text-base sm:text-sm"
            placeholder="Клиент, № заказа или номер машины"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        {view === "queue" ? (
          <div className="flex flex-wrap items-center gap-2">
            {(
              [
                ["", "Все"],
                [today, "Сегодня"],
                [shiftIsoDate(today, 1), "Завтра"],
              ] as const
            ).map(([value, label]) => (
              <Chip key={label} active={queueDay === value} onClick={() => setQueueDay(value)}>
                {label}
              </Chip>
            ))}
            <Input
              type="date"
              aria-label="Плановый день"
              value={queueDay}
              onChange={(event) => setQueueDay(event.target.value)}
              className="h-8 w-auto text-xs"
            />
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {(
              [
                ["Сегодня", today, today],
                ["Вчера", shiftIsoDate(today, -1), shiftIsoDate(today, -1)],
                ["7 дней", shiftIsoDate(today, -6), today],
              ] as const
            ).map(([label, from, to]) => (
              <Chip key={label} active={range.from === from && range.to === to} onClick={() => setRange({ from, to })}>
                {label}
              </Chip>
            ))}
            <Input
              type="date"
              aria-label="С даты"
              value={range.from}
              max={range.to}
              onChange={(event) => setRange((current) => ({ ...current, from: event.target.value || today }))}
              className="h-8 w-auto text-xs"
            />
            <span className="text-xs text-[var(--muted-foreground)]">—</span>
            <Input
              type="date"
              aria-label="По дату"
              value={range.to}
              min={range.from}
              onChange={(event) => setRange((current) => ({ ...current, to: event.target.value || today }))}
              className="h-8 w-auto text-xs"
            />
          </div>
        )}
      </div>

      {view === "queue" ? (
        <QueueList
          queue={queue}
          today={today}
          selectedId={selectedId}
          selectable={canConfirm}
          onSelect={select}
          filtered={Boolean(queueDay || debouncedSearch)}
        />
      ) : (
        <HistoryList history={history} today={today} onPrint={print} />
      )}

      <WaybillSettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </AppShell>
  );
}

type Paged = ReturnType<typeof usePagedApi<LoaderOrder>>;

function QueueList({
  queue,
  today,
  selectedId,
  selectable,
  onSelect,
  filtered,
}: {
  queue: Paged;
  today: string;
  selectedId: number | null;
  selectable: boolean;
  onSelect: (order: LoaderOrder) => void;
  filtered: boolean;
}) {
  if (queue.loading && queue.items.length === 0) return <DataGate loading error="" onRetry={queue.reload} />;
  if (queue.error) return <DataGate loading={false} error={queue.error} onRetry={queue.reload} />;
  if (queue.items.length === 0) {
    return (
      <Card className="flex flex-col items-center gap-2 py-14 text-center">
        <PackageCheck className="size-8 text-[var(--muted-foreground)]" />
        <div className="font-medium">{filtered ? "Ничего не найдено" : "Все заказы отгружены"}</div>
        <p className="text-sm text-[var(--muted-foreground)]">
          {filtered ? "Измените день или поиск." : "Новые подтверждённые заказы появятся здесь."}
        </p>
      </Card>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div
        role={selectable ? "radiogroup" : undefined}
        aria-label="Заказы к отгрузке"
        className="grid gap-3 lg:grid-cols-2"
      >
        {queue.items.map((order) => {
          const active = order.id === selectedId;
          const planned = order.arrival_date ?? order.created_at.slice(0, 10);
          return (
            <button
              key={order.id}
              type="button"
              role={selectable ? "radio" : undefined}
              aria-checked={selectable ? active : undefined}
              disabled={!selectable}
              onClick={() => onSelect(order)}
              className={cn(
                "flex flex-col gap-2 rounded-xl border bg-[var(--card)] p-4 text-left shadow-card transition-[box-shadow,background-color] disabled:cursor-default",
                selectable && "hover:bg-[var(--muted)]/40",
                active && "bg-[var(--primary)]/5 outline outline-2 outline-[var(--primary)]",
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="text-base font-semibold">
                    №{order.id} · {order.client_name}
                  </div>
                  <div className="mt-0.5 text-sm text-[var(--muted-foreground)]">
                    <TransportLabel order={order} />
                  </div>
                </div>
                {active ? (
                  <CheckCircle2 aria-hidden className="size-6 shrink-0 text-[var(--primary)]" />
                ) : (
                  <span
                    className={cn(
                      "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
                      planned < today ? "bg-[var(--warning)]/15 text-[var(--warning)]" : "bg-[var(--muted)]",
                    )}
                  >
                    {dayLabel(planned, today)}
                  </span>
                )}
              </div>
              <ul className="text-sm">
                {order.items.map((item, index) => (
                  <li key={index} className="flex justify-between gap-3">
                    <span className="min-w-0 truncate">{item.label}</span>
                    <span className="shrink-0 font-medium tabular-nums">{bagsLabel(item.quantity)}</span>
                  </li>
                ))}
              </ul>
              <div className="flex items-center justify-between gap-3 border-t pt-2 text-sm">
                <span className="text-[var(--muted-foreground)] tabular-nums">
                  {bagsLabel(order.bags)} · {Number(order.total_kg)} кг
                </span>
                <span className="font-semibold tabular-nums">{amountLabel(order)}</span>
              </div>
            </button>
          );
        })}
      </div>
      <LoadMore
        shown={queue.items.length}
        total={queue.count}
        hasMore={queue.hasMore}
        loading={queue.loadingMore}
        onClick={queue.loadMore}
      />
    </div>
  );
}

function HistoryList({
  history,
  today,
  onPrint,
}: {
  history: Paged;
  today: string;
  onPrint: (orderId: number) => void;
}) {
  if (history.loading && history.items.length === 0) return <DataGate loading error="" onRetry={history.reload} />;
  if (history.error) return <DataGate loading={false} error={history.error} onRetry={history.reload} />;
  if (history.items.length === 0) {
    return (
      <Card className="py-14 text-center text-sm text-[var(--muted-foreground)]">За этот период отгрузок нет.</Card>
    );
  }
  const totalBags = history.items.reduce((sum, order) => sum + order.bags, 0);
  return (
    <div className="flex flex-col gap-3">
      <div className="text-sm text-[var(--muted-foreground)]">
        {history.count} {pluralRu(history.count, ["отгрузка", "отгрузки", "отгрузок"])}
        {history.items.length === history.count && ` · ${bagsLabel(totalBags)}`}
      </div>
      <Card className="divide-y">
        {history.items.map((order) => {
          const shippedOn = order.shipped_at ? order.shipped_at.slice(0, 10) : today;
          return (
            <div
              key={order.id}
              className="grid grid-cols-[3.5rem_minmax(0,1fr)] items-center gap-x-3 gap-y-2 px-4 py-3 sm:grid-cols-[4rem_minmax(0,1fr)_auto]"
            >
              <div className="text-sm tabular-nums">
                <div className="font-semibold">{order.shipped_at ? formatTime(order.shipped_at) : "—"}</div>
                {shippedOn !== today && (
                  <div className="text-xs text-[var(--muted-foreground)]">{dayLabel(shippedOn, today)}</div>
                )}
              </div>
              <div className="min-w-0">
                <div className="font-medium">
                  №{order.id} · {order.client_name}
                </div>
                <div className="flex flex-wrap gap-x-3 text-sm text-[var(--muted-foreground)]">
                  <TransportLabel order={order} />
                  <span className="tabular-nums">{bagsLabel(order.bags)}</span>
                  <span className="tabular-nums">{amountLabel(order)}</span>
                </div>
              </div>
              {/* На телефоне кнопка — отдельной строкой: иначе клиент и номер машины обрезаются. */}
              <Button variant="outline" className="col-span-2 h-10 sm:col-span-1" onClick={() => onPrint(order.id)}>
                <Printer className="size-4" /> Накладная
              </Button>
            </div>
          );
        })}
      </Card>
      <LoadMore
        shown={history.items.length}
        total={history.count}
        hasMore={history.hasMore}
        loading={history.loadingMore}
        onClick={history.loadMore}
      />
    </div>
  );
}

export default function LoaderPage() {
  return (
    <RequirePerm perm="loader.view" title="Грузчик">
      <LoaderPageInner />
    </RequirePerm>
  );
}
