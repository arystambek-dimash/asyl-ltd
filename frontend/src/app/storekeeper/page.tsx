"use client";
import { useEffect, useRef, useState } from "react";
import { PackageCheck, RefreshCwOff } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { StorekeeperReturnCard } from "@/components/storekeeper/return-card";
import {
  closeVerdict,
  CloseReturnBar,
  StorekeeperClosedScreen,
  StorekeeperReturnScreen,
} from "@/components/storekeeper/return-screen";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DataGate } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { SearchInput } from "@/components/ui/search-input";
import { Tabs } from "@/components/ui/tabs";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import type { GoodsReturnItem, StorekeeperReturn } from "@/lib/types";
import { useConfirmAction } from "@/lib/use-confirm-action";
import { useDebounced } from "@/lib/use-debounced";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { apiUrl } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type View = "pending" | "closed";
type Paged = ReturnType<typeof usePagedApi<StorekeeperReturn>>;

const RETURNS_URL = "/storekeeper/returns/";
/** Возвраты создают менеджеры, закрыть может второй кладовщик — сверяемся тихо. */
const QUEUE_POLL_MS = 15_000;

/** Что сделает «Исправить»: закрытие откатывается, возврат снова ждёт приёмки с прежними числами. */
function reopenText(row: StorekeeperReturn) {
  const back = "возврат снова будет ждать приёмки с прежними числами";
  return row.status === "cancelled"
    ? `Склад не менялся — ${back}.`
    : `Мешки уйдут со склада «${row.warehouse_name}», долг и касса вернутся как были, ${back}.`;
}

function StorekeeperPageInner() {
  const { me } = useAuth();
  const canConfirm = can(me, "storekeeper.confirm");
  const [view, setView] = useState<View>("pending");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search.trim());
  const queue = usePagedApi<StorekeeperReturn>(apiUrl(RETURNS_URL, { state: "pending", search: debouncedSearch }));
  const history = usePagedApi<StorekeeperReturn>(
    view === "closed" ? apiUrl(RETURNS_URL, { state: "closed", search: debouncedSearch }) : null,
  );
  // Возврат, каким его открыли (и последний ответ сервера); на экране — свежая строка очереди.
  const [openedId, setOpenedId] = useState<number | null>(null);
  const [snapshot, setSnapshot] = useState<StorekeeperReturn | null>(null);
  const [closed, setClosed] = useState<StorekeeperReturn | null>(null);
  const [busy, setBusy] = useState(false);
  // Второе нажатие, пока идёт запрос, не должно уйти вторым запросом.
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  // Открытый возврат ушёл из очереди без нас (закрыт с другого устройства или отменён менеджером).
  const [lost, setLost] = useState("");
  // Строки, где кладовщик набирает «сколько пришло», но ещё не принял: закрывать рано.
  const [editingIds, setEditingIds] = useState<ReadonlySet<number>>(() => new Set());

  function setItemEditing(item: GoodsReturnItem, editing: boolean) {
    setEditingIds((current) => {
      if (current.has(item.id) === editing) return current;
      const next = new Set(current);
      if (editing) next.add(item.id);
      else next.delete(item.id);
      return next;
    });
  }

  /** «Закрыть возврат» и «Отменить возврат» с экрана приёмки: возврат уходит в историю. */
  async function finish(row: StorekeeperReturn, action: "close" | "cancel") {
    try {
      const { data } = await api.post<StorekeeperReturn>(`${RETURNS_URL}${row.id}/${action}/`, {});
      setOpenedId(null);
      setError("");
      setClosed(data);
      // Ответ — истина: возврат ушёл из очереди, опрос в полёте его не вернёт.
      queue.applyItems((rows) => rows.filter((item) => item.id !== data.id));
    } catch (cause) {
      // «Не всё проверено», «уже не помещается»: причина остаётся на экране и после окна,
      // строки могли поменяться — сверяемся тихо.
      setError(apiError(cause));
      void queue.refresh();
      throw cause;
    }
  }
  const closeAction = useConfirmAction<StorekeeperReturn>((row) => finish(row, "close"));
  const cancelAction = useConfirmAction<StorekeeperReturn>((row) => finish(row, "cancel"));
  // «Исправить» в истории: закрытие откатывается, возврат снова в очереди — сразу на экран приёмки.
  const reopenAction = useConfirmAction<StorekeeperReturn>(async (row) => {
    try {
      const { data } = await api.post<StorekeeperReturn>(`${RETURNS_URL}${row.id}/reopen/`, {});
      queue.applyItems((rows) => [data, ...rows.filter((item) => item.id !== data.id)]);
      setView("pending");
      openReturn(data);
    } catch (cause) {
      // Возврат уже исправили с другого устройства — история могла устареть.
      void history.refresh();
      throw cause;
    }
  });
  const working = busy || closeAction.busy || cancelAction.busy;
  // Открыто окно «Закрыть» или «Отменить»: возврат на экране не трогаем.
  const confirming = closeAction.item !== null || cancelAction.item !== null;
  const opened =
    queue.items.find((row) => row.id === openedId) ?? (openedId !== null && (working || confirming) ? snapshot : null);

  // Тихий опрос очереди: без индикатора, ошибка не заменяет список. Пока идёт
  // приёмка строки или открыто окно закрытия или отмены, очередь не трогаем.
  useVisiblePolling(() => queue.refresh(), QUEUE_POLL_MS, view === "pending" && !working && !closed && !confirming);

  // Возврат, открытый у этого кладовщика, закрыли или отменили с другого
  // устройства — назад к списку с пояснением, а не молча.
  useEffect(() => {
    if (openedId === null || working || confirming || queue.loading || queue.items.some((row) => row.id === openedId))
      return;
    setOpenedId(null);
    setError("");
    setLost(`Возврат №${openedId} уже не ждёт приёмки — его закрыли или отменили с другого устройства.`);
  }, [openedId, working, confirming, queue.items, queue.loading]);

  function openReturn(row: StorekeeperReturn) {
    setSnapshot(row);
    setOpenedId(row.id);
    setEditingIds(new Set());
    setError("");
    setLost("");
  }

  function backToList() {
    setOpenedId(null);
    setClosed(null);
    setEditingIds(new Set());
    setError("");
  }

  /** Приёмка строки: ответ — свежая строка очереди, экран применяет её, а не перечитывает. */
  async function acceptItem(row: StorekeeperReturn, item: GoodsReturnItem, bags: number): Promise<boolean> {
    if (inFlight.current) return false;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<StorekeeperReturn>(`${RETURNS_URL}${row.id}/items/${item.id}/`, {
        accepted_bags: bags,
      });
      setSnapshot(data);
      queue.applyItems((rows) => rows.map((current) => (current.id === data.id ? data : current)));
      return true;
    } catch (cause) {
      setError(apiError(cause));
      void queue.refresh();
      return false;
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (closed) {
    return (
      <AppShell title="Кладовщик" section="Работа">
        <StorekeeperClosedScreen row={closed} onBack={backToList} />
      </AppShell>
    );
  }
  if (opened) {
    const closing = closeAction.item ?? opened;
    const editing = opened.items.filter((item) => editingIds.has(item.id));
    const verdict = closeVerdict(closing, editing);
    return (
      <AppShell
        title="Кладовщик"
        section="Работа"
        footer={
          canConfirm && (
            <CloseReturnBar row={opened} editing={editing} busy={working} onClose={() => closeAction.open(opened)} />
          )
        }
      >
        <StorekeeperReturnScreen
          row={opened}
          canConfirm={canConfirm}
          busy={working}
          error={error}
          editingIds={editingIds}
          onEditingChange={setItemEditing}
          onBack={backToList}
          onAccept={(item, bags) => acceptItem(opened, item, bags)}
          onCancel={() => cancelAction.open(opened)}
        />
        <ConfirmDialog
          {...closeAction.dialog}
          title={`Закрыть возврат №${closing.id}?`}
          description={
            verdict.outcome === "cancelled"
              ? `${verdict.text}. Склад не изменится.`
              : `${verdict.text}. Принятые мешки лягут на склад «${closing.warehouse_name}». Если ошиблись — «Исправить» в «Истории».`
          }
          confirmLabel="Закрыть возврат"
          confirmVariant="default"
        />
        <ConfirmDialog
          {...cancelAction.dialog}
          title={`Отменить возврат №${(cancelAction.item ?? opened).id}?`}
          description="Мешки не принимаются, склад не изменится. Возврат уйдёт в «Историю» отменённым."
          confirmLabel="Отменить возврат"
        />
      </AppShell>
    );
  }

  return (
    <AppShell title="Кладовщик" section="Работа">
      <div className="mx-auto flex w-full min-w-0 max-w-3xl flex-col gap-4">
        <Tabs
          variant="segment"
          label="Приёмка или история"
          active={view}
          onChange={(key) => setView(key as View)}
          className="self-start"
          tabs={[
            { key: "pending", label: "Ждут приёмки", count: queue.count },
            { key: "closed", label: "История" },
          ]}
        />
        {lost && (
          <p
            role="status"
            className="rounded-xl border border-[var(--warning)]/40 bg-[var(--warning)]/10 px-4 py-3 text-sm text-[var(--foreground)]"
          >
            {lost}
          </p>
        )}
        <SearchInput
          aria-label="Поиск"
          size="lg"
          placeholder="Клиент, телефон или № возврата"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        {view === "pending" && queue.refreshError && (
          // Опрос не удался: список остаётся последним полученным, пометка — маленькая.
          <p role="status" className="flex items-center gap-1.5 text-xs text-[var(--muted-foreground)]">
            <RefreshCwOff className="size-3.5" /> Не удалось обновить — список мог устареть, повторим сами.
          </p>
        )}
        {view === "pending" ? (
          <ReturnList
            list={queue}
            onOpen={openReturn}
            empty={
              debouncedSearch
                ? { title: "Ничего не найдено", text: "Измените поиск." }
                : { title: "Возвратов на приёмку нет", text: "Возвраты, созданные менеджерами, появятся здесь." }
            }
          />
        ) : (
          <ReturnList
            list={history}
            onFix={canConfirm ? reopenAction.open : undefined}
            empty={
              debouncedSearch
                ? { title: "Ничего не найдено", text: "Измените поиск." }
                : { title: "Закрытых возвратов пока нет", text: "Закрытые и отменённые возвраты появятся здесь." }
            }
          />
        )}
      </div>
      <ConfirmDialog
        {...reopenAction.dialog}
        title={`Исправить возврат №${reopenAction.item?.id ?? ""}?`}
        description={reopenAction.item ? reopenText(reopenAction.item) : undefined}
        confirmLabel="Вернуть на приёмку"
        confirmVariant="default"
      />
    </AppShell>
  );
}

/** Карточки возвратов страницами; в очереди карточка открывает экран приёмки, в истории — «Исправить». */
function ReturnList({
  list,
  onOpen,
  onFix,
  empty,
}: {
  list: Paged;
  onOpen?: (row: StorekeeperReturn) => void;
  onFix?: (row: StorekeeperReturn) => void;
  empty: { title: string; text: string };
}) {
  if (list.loading && list.items.length === 0) return <DataGate loading error="" onRetry={list.reload} />;
  if (list.error) return <DataGate loading={false} error={list.error} onRetry={list.reload} />;
  if (list.items.length === 0) {
    return (
      <Card className="flex flex-col items-center gap-2 py-14 text-center">
        <PackageCheck className="size-8 text-[var(--muted-foreground)]" />
        <div className="font-medium">{empty.title}</div>
        <p className="text-sm text-[var(--muted-foreground)]">{empty.text}</p>
      </Card>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="grid min-w-0 gap-3 sm:grid-cols-2">
        {list.items.map((row) => (
          <StorekeeperReturnCard key={row.id} row={row} onOpen={onOpen} onFix={onFix} />
        ))}
      </div>
      <LoadMore
        shown={list.items.length}
        total={list.count}
        hasMore={list.hasMore}
        loading={list.loadingMore}
        onClick={list.loadMore}
      />
    </div>
  );
}

export default function StorekeeperPage() {
  return (
    <RequirePerm perm="storekeeper.view" title="Кладовщик">
      <StorekeeperPageInner />
    </RequirePerm>
  );
}
