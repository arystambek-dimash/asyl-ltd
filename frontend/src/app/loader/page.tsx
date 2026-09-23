"use client";
import { useEffect, useState } from "react";
import {
  ClipboardCopy,
  ClipboardPaste,
  PackageCheck,
  Printer,
  RefreshCwOff,
  Search,
  Settings2,
  Undo2,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { LoaderOrderCard, bagsWord } from "@/components/loader/loader-order-card";
import { LoaderOrderScreen, LoaderShippedScreen } from "@/components/loader/loader-order-screen";
import { RailReportSheet } from "@/components/loader/rail-report-sheet";
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
import {
  loaderUrl,
  openWaybill,
  readStoredLoaderTransport,
  shiftIsoDate,
  storeLoaderTransport,
  type LoaderOrder,
} from "@/lib/loader";
import {
  groupByPlannedDay,
  inHistoryRange,
  initialLoaderTransport,
  inQueueFilter,
  LOADER_TRANSPORTS,
  loaderTransports,
  plannedDay,
  shippedDay,
  shortDate,
  withHistoryRow,
  withQueueRow,
  type LoaderQueueFilter,
  type LoaderTransport,
} from "@/lib/loader-groups";
import { EMPTY_TRANSPORT_PAIR, transportChanges, transportPairOf, type TransportPair } from "@/lib/plates";
import { copyText } from "@/lib/rail-report";
import { showToast } from "@/lib/toast";
import { useDebounced } from "@/lib/use-debounced";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn, pluralRu, todayLocalIsoDate } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type View = "queue" | "history";
type Paged = ReturnType<typeof usePagedApi<LoaderOrder>>;

/** Очередь меняют другие устройства (второй грузчик, камеры) — сверяемся тихо. */
const QUEUE_POLL_MS = 15_000;

function LoaderPageInner() {
  const { me } = useAuth();
  // Вкладки «Фуры | Вагоны» — области грузчика; чужой транспорт он не видит вовсе.
  const transports = loaderTransports(me);
  const [pickedTransport, setPickedTransport] = useState(() => (me ? readStoredLoaderTransport(me.id) : null));
  const transport = initialLoaderTransport(transports, pickedTransport);
  const canEditSettings = can(me, "sys_permissions.manage");
  const today = todayLocalIsoDate();
  const [view, setView] = useState<View>("queue");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search.trim());
  // Очередь открывается на сегодняшнем дне: месячная просрочка не должна закрывать работу.
  const [queueFilter, setQueueFilter] = useState<"today" | "tomorrow" | "overdue" | "all" | "date">("today");
  const [queueDay, setQueueDay] = useState(today);
  const [range, setRange] = useState({ from: today, to: today });
  // Заказ, каким его открыли; на экране — свежая строка очереди.
  const [openedOrder, setOpenedOrder] = useState<LoaderOrder | null>(null);
  const openedId = openedOrder?.id ?? null;
  // Пара заказа, какой её увидел оператор, и набранная им: уходит только исправленное.
  const [openedNumbers, setOpenedNumbers] = useState<TransportPair>(EMPTY_TRANSPORT_PAIR);
  const [numbers, setNumbers] = useState<TransportPair>(EMPTY_TRANSPORT_PAIR);
  const [shipped, setShipped] = useState<LoaderOrder | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [undone, setUndone] = useState("");
  // Открытый заказ ушёл из очереди без нас (отгружен с другого устройства).
  const [lost, setLost] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  // «Вставить отчёт» (orderId null) или «Отгрузить по отчёту» открытого заказа.
  const [railSheet, setRailSheet] = useState<{ orderId: number | null } | null>(null);

  const queueParams: LoaderQueueFilter = {
    day: queueFilter === "overdue" || queueFilter === "all" ? "" : queueDay,
    overdue: queueFilter === "overdue" ? "1" : "",
    search: debouncedSearch,
  };
  const queue = usePagedApi<LoaderOrder>(transport ? loaderUrl("queue", { transport, ...queueParams }) : null);
  // Просроченные не теряются: их число видно на отдельной кнопке.
  const overdue = useApi<{ count: number }>(
    transport && queueFilter !== "overdue"
      ? loaderUrl("queue", { transport, overdue: "1", page: "1", page_size: "1" })
      : null,
  );
  const overdueCount = queueFilter === "overdue" ? queue.count : (overdue.data?.count ?? 0);
  // Счётчик соседней вкладки — тем же фильтром очереди, одной строкой.
  const otherTransport = transports.find((key) => key !== transport) ?? null;
  const otherQueue = useApi<{ count: number }>(
    otherTransport
      ? loaderUrl("queue", { transport: otherTransport, ...queueParams, page: "1", page_size: "1" })
      : null,
  );
  const history = usePagedApi<LoaderOrder>(
    view === "history" && transport
      ? loaderUrl("history", { transport, date_from: range.from, date_to: range.to, search: debouncedSearch })
      : null,
  );
  // Пока идёт отгрузка, опрос, ушедший до нажатия, мог уже убрать строку —
  // экран держится на открытом заказе до ответа.
  const opened = queue.items.find((order) => order.id === openedId) ?? (busy ? openedOrder : null);
  const canConfirm = can(me, "loader.confirm") && transport !== null;

  // Тихий опрос: без индикатора загрузки, ошибка не заменяет список. Пока идёт
  // отгрузка или открыто окно, очередь не трогаем.
  useVisiblePolling(
    () => Promise.all([queue.refresh(), overdue.reload(), otherQueue.reload()]),
    QUEUE_POLL_MS,
    view === "queue" && transport !== null && !busy && !settingsOpen && !shipped && !railSheet,
  );

  // Заказ, открытый у этого грузчика, отгрузили с другого устройства — назад
  // к списку с пояснением, а не молча. Во время своей отгрузки молчим: её
  // исход скажет ответ, а не опрос.
  useEffect(() => {
    if (openedId === null || busy || railSheet || queue.loading || queue.items.some((order) => order.id === openedId))
      return;
    setOpenedOrder(null);
    setNumbers(EMPTY_TRANSPORT_PAIR);
    setError("");
    setLost(`Заказ №${openedId} уже не ждёт отгрузки — его отгрузили с другого устройства или перенесли.`);
  }, [openedId, busy, railSheet, queue.items, queue.loading]);

  function refreshCounters() {
    void overdue.reload();
    void otherQueue.reload();
  }

  function switchTransport(key: LoaderTransport) {
    setPickedTransport(key);
    if (me) storeLoaderTransport(key, me.id);
    backToList();
    setUndone("");
    setLost("");
  }

  function openOrder(order: LoaderOrder) {
    setShipped(null);
    setError("");
    setUndone("");
    setLost("");
    // Номер подставляем из заказа, но последнее слово за оператором.
    setOpenedNumbers(transportPairOf(order));
    setNumbers(transportPairOf(order));
    setOpenedOrder(order);
  }

  function backToList() {
    setOpenedOrder(null);
    setShipped(null);
    setError("");
    setNumbers(EMPTY_TRANSPORT_PAIR);
  }

  async function confirm() {
    if (!opened || busy) return;
    setBusy(true);
    setError("");
    try {
      // Неисправленный номер не уходит: чужую правку, сделанную после открытия заказа,
      // он не перетрёт. Пустой прицеп — «стереть» (у вагона прицепа нет вовсе).
      const { data } = await api.post<LoaderOrder>(
        `/loader/orders/${opened.id}/dispatch/`,
        transportChanges(openedNumbers, numbers),
      );
      setShipped(data);
      setOpenedOrder(null);
      setNumbers(EMPTY_TRANSPORT_PAIR);
      setLost("");
      // Ответ отгрузки — истина: заказ ушёл из очереди, опрос в полёте его не вернёт.
      queue.applyItems((rows) => rows.filter((row) => row.id !== data.id));
      refreshCounters();
    } catch (cause) {
      setError(apiError(cause));
      // Заказ мог уехать с другого устройства — сверимся с очередью.
      void queue.refresh();
    } finally {
      setBusy(false);
    }
  }

  /** Отмена ошибочной отгрузки: заказ возвращается в очередь, мешки — на склад. */
  async function undo(order: LoaderOrder) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<LoaderOrder>(`/loader/orders/${order.id}/rollback/`, {});
      setShipped(null);
      setUndone(`Отгрузка заказа №${order.id} отменена — он снова в очереди.`);
      // Ответ отмены — строка очереди: заказ возвращается туда, если подходит
      // под показанный день, и уходит из истории. Под поиском правило знает
      // только сервер — сверяемся тихо.
      queue.applyItems((rows) => (inQueueFilter(data, queueParams, today) ? withQueueRow(rows, data) : rows));
      if (queueParams.search) void queue.refresh();
      if (view === "history") history.applyItems((rows) => rows.filter((row) => row.id !== data.id));
      refreshCounters();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  /** Отчёт о вагонах проведён: ответ — строка истории, экран применяет её, а не перечитывает. */
  function railApplied(row: LoaderOrder) {
    setRailSheet(null);
    setShipped(row);
    setOpenedOrder(null);
    setNumbers(EMPTY_TRANSPORT_PAIR);
    setError("");
    setUndone("");
    setLost("");
    queue.applyItems((rows) => rows.filter((item) => item.id !== row.id));
    if (view === "history" && inHistoryRange(row, range, debouncedSearch, today)) {
      history.applyItems((rows) => withHistoryRow(rows, row));
    }
    refreshCounters();
  }

  async function copyReport(order: LoaderOrder) {
    const copied = await copyText(order.rail_report_text ?? "");
    showToast(
      copied ? `Отчёт по заказу №${order.id} скопирован` : "Не удалось скопировать — браузер не дал доступ к буферу",
      copied ? "success" : "error",
    );
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
  const railReportSheet = railSheet && (
    <RailReportSheet orderId={railSheet.orderId} onClose={() => setRailSheet(null)} onApplied={railApplied} />
  );

  if (shipped) {
    return (
      <AppShell title="Грузчик" section="Работа">
        <LoaderShippedScreen
          order={shipped}
          busy={busy}
          onPrint={() => void print(shipped.id)}
          onBack={backToList}
          onUndo={() => void undo(shipped)}
        />
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
          numbers={numbers}
          onNumbers={setNumbers}
          onBack={backToList}
          onConfirm={confirm}
          onPrint={() => void print(opened.id)}
          onShipByReport={() => setRailSheet({ orderId: opened.id })}
        />
        {railReportSheet}
      </AppShell>
    );
  }

  if (!transport) {
    return (
      <AppShell title="Грузчик" section="Работа">
        <Card className="mx-auto flex w-full max-w-3xl flex-col items-center gap-2 py-14 text-center">
          <PackageCheck className="size-8 text-[var(--muted-foreground)]" />
          <div className="font-medium">Не выдана ни одна область</div>
          <p className="max-w-sm text-sm text-[var(--muted-foreground)]">
            Попросите администратора выдать право «Грузчик: Фуры» или «Грузчик: Вагоны».
          </p>
        </Card>
      </AppShell>
    );
  }

  return (
    <AppShell
      title="Грузчик"
      section="Работа"
      tabs={
        // Одна область — без переключателя: грузчику нечего выбирать.
        transports.length > 1 ? (
          <Tabs
            label="Транспорт"
            active={transport}
            onChange={(key) => switchTransport(key as LoaderTransport)}
            tabs={LOADER_TRANSPORTS.filter((tab) => transports.includes(tab.key)).map((tab) => ({
              key: tab.key,
              label: tab.label,
              count: tab.key === transport ? queue.count : otherQueue.data?.count,
            }))}
          />
        ) : undefined
      }
      actions={
        canEditSettings && (
          <Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)} aria-label="Настройки накладной">
            <Settings2 className="size-4" /> <span className="hidden sm:inline">Накладная</span>
          </Button>
        )
      }
    >
      <div className="mx-auto flex w-full min-w-0 max-w-3xl flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Tabs
            variant="segment"
            label="Очередь или история"
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
          {/* Отчёт Джин-Сина о вагонах: предпросмотр, разбор и «Провести» — в листе. */}
          {transport === "train" && (
            <Button variant="outline" className="h-10" onClick={() => setRailSheet({ orderId: null })}>
              <ClipboardPaste className="size-4" /> Вставить отчёт
            </Button>
          )}
        </div>
        {error && (
          <p role="alert" className="rounded-xl bg-[var(--destructive)]/10 px-4 py-3 text-sm text-[var(--destructive)]">
            {error}
          </p>
        )}
        {undone && (
          <p
            role="status"
            className="rounded-xl border border-[var(--success)]/30 bg-[var(--success)]/10 px-4 py-3 text-sm text-[var(--success)]"
          >
            {undone}
          </p>
        )}
        {lost && (
          <p
            role="status"
            className="rounded-xl border border-[var(--warning)]/40 bg-[var(--warning)]/10 px-4 py-3 text-sm text-[var(--foreground)]"
          >
            {lost}
          </p>
        )}
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-5 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            aria-label="Поиск"
            className="h-12 pl-10 text-base"
            placeholder={transport === "train" ? "Номер вагона или клиент" : "Номер машины или клиент"}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        {view === "queue" ? (
          <div className="flex flex-wrap items-center gap-2">
            {(
              [
                ["today", "Сегодня", today],
                ["tomorrow", "Завтра", shiftIsoDate(today, 1)],
                ["all", "Все", ""],
              ] as const
            ).map(([key, label, day]) => (
              <Chip
                key={key}
                active={queueFilter === key}
                onClick={() => {
                  setQueueFilter(key);
                  if (day) setQueueDay(day);
                }}
              >
                {label}
              </Chip>
            ))}
            {overdueCount > 0 && (
              <Chip
                active={queueFilter === "overdue"}
                className={queueFilter === "overdue" ? "" : "border-[var(--destructive)]/40 text-[var(--destructive)]"}
                onClick={() => setQueueFilter("overdue")}
              >
                Просрочено · {overdueCount}
              </Chip>
            )}
            <Input
              type="date"
              aria-label="Плановый день"
              value={queueFilter === "overdue" || queueFilter === "all" ? "" : queueDay}
              onChange={(event) => {
                if (!event.target.value) return;
                setQueueDay(event.target.value);
                setQueueFilter("date");
              }}
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

        {view === "queue" && queue.refreshError && (
          // Опрос не удался: список остаётся последним полученным, пометка — маленькая.
          <p role="status" className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
            <RefreshCwOff className="size-3.5" /> Не удалось обновить — список мог устареть, повторим сами.
          </p>
        )}
        {view === "queue" ? (
          <QueueList
            queue={queue}
            today={today}
            onOpen={openOrder}
            filtered={queueFilter !== "all" || Boolean(debouncedSearch)}
          />
        ) : (
          <HistoryList
            history={history}
            today={today}
            busy={busy}
            onPrint={print}
            onUndo={undo}
            onCopyReport={(order) => void copyReport(order)}
          />
        )}
      </div>

      <WaybillSettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      {railReportSheet}
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
        <section key={group.day} className="flex min-w-0 flex-col gap-3">
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
          <div className="grid min-w-0 gap-3 sm:grid-cols-2">
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
  busy,
  onPrint,
  onUndo,
  onCopyReport,
}: {
  history: Paged;
  today: string;
  busy: boolean;
  onPrint: (orderId: number) => void;
  onUndo: (order: LoaderOrder) => void;
  onCopyReport: (order: LoaderOrder) => void;
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
        const shippedOn = shippedDay(order, today);
        return (
          <div key={order.id} className="flex min-w-0 flex-col gap-2">
            {shippedOn !== today && (
              <span className="text-xs font-medium text-[var(--muted-foreground)] tabular-nums">
                {shortDate(shippedOn)}
              </span>
            )}
            <LoaderOrderCard order={order} />
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" className="h-11 flex-1" onClick={() => onPrint(order.id)}>
                <Printer className="size-4" /> Накладная
              </Button>
              {/* Отгрузка по отчёту — снова текстом в формате владельца (для WhatsApp). */}
              {order.rail_report_text && (
                <Button variant="outline" className="h-11 flex-1" onClick={() => onCopyReport(order)}>
                  <ClipboardCopy className="size-4" /> Скопировать отчёт
                </Button>
              )}
              {/* Ошибочную отгрузку грузчик отменяет сам, пока она свежая. */}
              {order.can_rollback && (
                <Button variant="ghost" className="h-11" disabled={busy} onClick={() => onUndo(order)}>
                  <Undo2 className="size-4" /> Отменить
                </Button>
              )}
            </div>
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
