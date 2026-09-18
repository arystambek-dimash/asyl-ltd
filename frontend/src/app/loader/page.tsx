"use client";
import { useState } from "react";
import { PackageCheck, Printer, Search, Settings2 } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { LoaderOrderCard, bagsWord } from "@/components/loader/loader-order-card";
import { LoaderOrderScreen, LoaderShippedScreen } from "@/components/loader/loader-order-screen";
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
import { groupByPlannedDay, plannedDay, shortDate } from "@/lib/loader-groups";
import { useDebounced } from "@/lib/use-debounced";
import { usePagedApi } from "@/lib/use-paged-api";
import { cn, pluralRu, todayLocalIsoDate } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type View = "queue" | "history";
type Paged = ReturnType<typeof usePagedApi<LoaderOrder>>;

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
  const [openedId, setOpenedId] = useState<number | null>(null);
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
  const opened = queue.items.find((order) => order.id === openedId) ?? null;
  const needsNumber = opened?.transport_type === "truck" && !opened.truck_number;

  function openOrder(order: LoaderOrder) {
    setShipped(null);
    setError("");
    setTruckNumber("");
    setOpenedId(order.id);
  }

  function backToList() {
    setOpenedId(null);
    setShipped(null);
    setError("");
    setTruckNumber("");
  }

  async function confirm() {
    if (!opened || busy) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<LoaderOrder>(`/loader/orders/${opened.id}/dispatch/`, {
        truck_number: needsNumber ? truckNumber : "",
      });
      setShipped(data);
      setOpenedId(null);
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

  // Экран заказа и экран подтверждения занимают всю страницу: у грузчика
  // в руках одна задача, список и вкладки в этот момент только мешают.
  if (shipped) {
    return (
      <AppShell title="Грузчик" section="Работа">
        <LoaderShippedScreen order={shipped} onPrint={() => void print(shipped.id)} onBack={backToList} />
      </AppShell>
    );
  }
  if (opened) {
    return (
      <AppShell title="Грузчик" section="Работа">
        <LoaderOrderScreen
          order={opened}
          day={plannedDay(opened)}
          today={today}
          canConfirm={canConfirm}
          busy={busy}
          error={error}
          needsNumber={Boolean(needsNumber)}
          number={truckNumber}
          onNumber={setTruckNumber}
          onBack={backToList}
          onConfirm={confirm}
          onPrint={() => void print(opened.id)}
        />
      </AppShell>
    );
  }

  return (
    <AppShell
      title="Грузчик"
      section="Работа"
      tabs={
        <Tabs
          active={view}
          onChange={(key) => {
            setView(key as View);
            backToList();
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
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4">
        {error && (
          <p role="alert" className="rounded-xl bg-[var(--destructive)]/10 px-4 py-3 text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-5 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            aria-label="Поиск"
            className="h-12 pl-10 text-base"
            placeholder="Номер машины или клиент"
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

        {view === "queue" ? (
          <QueueList queue={queue} today={today} onOpen={openOrder} filtered={Boolean(queueDay || debouncedSearch)} />
        ) : (
          <HistoryList history={history} today={today} onPrint={print} />
        )}
      </div>

      <WaybillSettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </AppShell>
  );
}

/** Очередь по дням: просроченные сверху, дальше сегодня и план. */
function QueueList({
  queue,
  today,
  onOpen,
  filtered,
}: {
  queue: Paged;
  today: string;
  onOpen: (order: LoaderOrder) => void;
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
  const groups = groupByPlannedDay(queue.items, today);
  return (
    <div className="flex flex-col gap-5">
      {groups.map((group) => (
        <section key={group.day} className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "rounded-lg px-2.5 py-1 text-xs font-bold uppercase tracking-wide",
                group.overdue
                  ? "bg-[var(--destructive)] text-white"
                  : group.label
                    ? "bg-[var(--foreground)] text-[var(--background)]"
                    : "bg-[var(--muted)] text-[var(--foreground)]",
              )}
            >
              {group.label || group.date}
            </span>
            <span className="text-xs text-[var(--muted-foreground)] tabular-nums">
              {group.label && `${group.date} · `}
              {group.orders.length} {pluralRu(group.orders.length, ["заказ", "заказа", "заказов"])}
            </span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {group.orders.map((order) => (
              <LoaderOrderCard key={order.id} order={order} overdue={group.overdue} onOpen={onOpen} />
            ))}
          </div>
        </section>
      ))}
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
        {history.items.length === history.count && ` · ${totalBags} ${bagsWord(totalBags)}`}
      </div>
      {history.items.map((order) => {
        const shippedOn = order.shipped_at ? order.shipped_at.slice(0, 10) : today;
        return (
          <div key={order.id} className="flex flex-col gap-2">
            {shippedOn !== today && (
              <span className="text-xs font-medium text-[var(--muted-foreground)] tabular-nums">
                {shortDate(shippedOn)}
              </span>
            )}
            <LoaderOrderCard order={order} />
            <Button variant="outline" className="h-11" onClick={() => onPrint(order.id)}>
              <Printer className="size-4" /> Накладная
            </Button>
          </div>
        );
      })}
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
