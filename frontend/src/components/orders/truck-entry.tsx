"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Lock, RefreshCw, Truck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { PlateInput, PlateSuggestions } from "@/components/ui/plate-input";
import { PlatePair } from "@/components/ui/transport-number";
import { api, apiError } from "@/lib/api";
import { countryFlag, findCountry } from "@/lib/countries";
import { withBack } from "@/lib/navigation";
import { sameTransportPair, transportChanges, transportPairOf, type TransportPair } from "@/lib/plates";
import type { TransportQueueRow } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn, formatIsoDayMonth, todayLocalIsoDate } from "@/lib/utils";

const BACK = "/orders?tab=trucks";
const COLUMN_LABEL = "text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]";

type Filter = "missing" | "today";
type RowStatus = { kind: "saving" } | { kind: "saved" } | { kind: "error"; message: string };
type Field = "truck" | "trailer";

function without<T>(record: Record<number, T>, id: number): Record<number, T> {
  if (!(id in record)) return record;
  const next = { ...record };
  delete next[id];
  return next;
}

function only<T>(record: Record<number, T>, keep: (id: number, value: T) => boolean): Record<number, T> {
  return Object.fromEntries(Object.entries(record).filter(([id, value]) => keep(Number(id), value)));
}

function RowState({ status, dirty, warning }: { status?: RowStatus; dirty: boolean; warning: string | null }) {
  if (!status) {
    // Набрали, но не нажали Enter (ушли мышью к другой фуре) — не теряем это из виду.
    return dirty ? <p className="text-xs text-[var(--warning)]">Не сохранено</p> : null;
  }
  if (status.kind === "saving") return <p className="text-xs text-[var(--muted-foreground)]">Сохраняем…</p>;
  if (status.kind === "error") {
    return (
      <p role="alert" className="text-xs text-[var(--destructive)]">
        {status.message}
      </p>
    );
  }
  return (
    <p role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
      <span className="flex items-center gap-1 text-[var(--success)]">
        <Check className="size-3.5" />
        Сохранено
      </span>
      {warning && (
        <span className="flex items-center gap-1 text-[var(--warning)]">
          <AlertTriangle className="size-3.5" />
          {warning}
        </span>
      )}
    </p>
  );
}

/**
 * Вкладка «Фуры» в «Заказах»: номера тягача и прицепа подтверждённым фурам —
 * с клавиатуры, строка за строкой. Enter в тягаче — к прицепу, Enter в прицепе —
 * сохранить и к тягачу следующей фуры, Esc — отменить правку строки. Ответ
 * сохранения применяется к своей строке, список не перечитывается.
 */
export function TruckEntrySection() {
  const [filter, setFilter] = useState<Filter>("missing");
  const queue = useApi<TransportQueueRow[]>(`/orders/transport-queue/?filter=${filter}`);
  // Ответы сохранений поверх списка — до следующего списка с сервера.
  const [applied, setApplied] = useState<Record<number, TransportQueueRow>>({});
  // Набранное живёт до сохранения или Esc: «Обновить» и смена фильтра его не стирают.
  const [drafts, setDrafts] = useState<Record<number, TransportPair>>({});
  const [statuses, setStatuses] = useState<Record<number, RowStatus>>({});
  // Строки, сохранённые после запроса списка: список мог уйти раньше записи — ответ сохранения новее.
  const savedSinceListed = useRef(new Set<number>());
  const fields = useRef(new Map<string, HTMLInputElement>());
  const today = todayLocalIsoDate();

  const rows = (queue.data ?? []).map((row) => applied[row.id] ?? row);
  const editable = rows.filter((row) => !row.transport_locked);
  const numbersOf = (row: TransportQueueRow) => drafts[row.id] ?? transportPairOf(row);

  // Свежий список сменяет ответы сохранений и отметки строк. Пока он грузится
  // или если не пришёл — на экране остаётся сохранённое, а не прежние номера.
  useEffect(() => {
    if (!queue.data) return;
    const keep = savedSinceListed.current;
    setApplied((current) => only(current, (id) => keep.has(id)));
    setStatuses((current) => only(current, (id, status) => keep.has(id) || status.kind === "saving"));
  }, [queue.data]);

  function relist(next?: Filter) {
    savedSinceListed.current = new Set();
    if (next) setFilter(next);
    else void queue.reload();
  }

  function register(id: number, field: Field) {
    return (node: HTMLInputElement | null) => {
      if (node) fields.current.set(`${id}:${field}`, node);
      else fields.current.delete(`${id}:${field}`);
    };
  }

  function focusField(id: number, field: Field) {
    fields.current.get(`${id}:${field}`)?.focus();
  }

  function edit(row: TransportQueueRow, patch: Partial<TransportPair>) {
    setDrafts((current) => ({ ...current, [row.id]: { ...(current[row.id] ?? transportPairOf(row)), ...patch } }));
    setStatuses((current) => without(current, row.id));
  }

  function revert(row: TransportQueueRow) {
    setDrafts((current) => without(current, row.id));
    setStatuses((current) => without(current, row.id));
  }

  async function save(row: TransportQueueRow) {
    const numbers = numbersOf(row);
    // Только исправленное: прицеп, дописанный с другого места, строка со старым списком не сотрёт.
    const changes = transportChanges(transportPairOf(row), numbers);
    if (!Object.keys(changes).length) return;
    setStatuses((current) => ({ ...current, [row.id]: { kind: "saving" } }));
    try {
      const { data } = await api.post<TransportQueueRow>(`/orders/${row.id}/transport/`, changes);
      savedSinceListed.current.add(row.id);
      setApplied((current) => ({ ...current, [row.id]: data }));
      // Черновик снимаем, только если строку не правили, пока шло сохранение.
      setDrafts((current) =>
        current[row.id] && !sameTransportPair(current[row.id], numbers) ? current : without(current, row.id),
      );
      setStatuses((current) => ({ ...current, [row.id]: { kind: "saved" } }));
    } catch (cause) {
      // 403 уже показан тостом (apiError отдаёт ""): строка остаётся «Не сохранено».
      const message = apiError(cause);
      setStatuses((current) =>
        message ? { ...current, [row.id]: { kind: "error", message } } : without(current, row.id),
      );
    }
  }

  function keyDown(row: TransportQueueRow, field: Field) {
    return (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "Escape") {
        event.preventDefault();
        revert(row);
        return;
      }
      if (event.key !== "Enter") return;
      event.preventDefault();
      if (field === "truck") {
        focusField(row.id, "trailer");
        return;
      }
      void save(row);
      const next = editable[editable.findIndex((item) => item.id === row.id) + 1];
      if (next) focusField(next.id, "truck");
    };
  }

  // Чип подставляет пару, сохраняет Enter — как у грузчика: промах не уходит клиенту уведомлением.
  function choose(row: TransportQueueRow, pair: TransportPair) {
    edit(row, pair);
    focusField(row.id, "trailer");
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Chip active={filter === "missing"} onClick={() => relist("missing")}>
            Без номера
          </Chip>
          <Chip active={filter === "today"} onClick={() => relist("today")}>
            Все на сегодня
          </Chip>
        </div>
        <Button size="sm" variant="outline" onClick={() => relist()} disabled={queue.loading}>
          <RefreshCw className="size-4" /> Обновить
        </Button>
      </div>
      <p className="hidden text-xs text-[var(--muted-foreground)] md:block">
        Enter в тягаче — к прицепу, Enter в прицепе — сохранить и к следующей фуре, Esc — отменить правку строки.
      </p>

      {queue.data !== null && queue.error && <ErrorAlert message={queue.error} onRetry={() => relist()} />}
      {queue.data === null ? (
        <DataGate loading={queue.loading} error={queue.error} onRetry={() => relist()} />
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed px-4 py-12 text-center">
          <Truck className="size-7 text-[var(--muted-foreground)]" />
          <div className="font-medium">
            {filter === "missing" ? "У всех подтверждённых фур есть номер" : "На сегодня фур нет"}
          </div>
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          <li aria-hidden className={cn(COLUMN_LABEL, "hidden gap-3 px-3 lg:flex")}>
            <span className="w-72 shrink-0">Заказ</span>
            <span className="grid flex-1 grid-cols-2 gap-2">
              <span>Тягач</span>
              <span>Прицеп (необязательно)</span>
            </span>
          </li>
          {rows.map((row) => {
            const numbers = numbersOf(row);
            const dirty = row.id in drafts && !sameTransportPair(numbers, transportPairOf(row));
            const iso = findCountry(row.client_country)?.iso;
            const suggestions = row.transport_locked ? [] : row.transport_suggestions;
            return (
              <li key={row.id} className="flex flex-col gap-2 rounded-xl border bg-[var(--card)] p-3 shadow-card">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
                  <div className="min-w-0 lg:w-72 lg:shrink-0">
                    <div className="flex min-w-0 items-center gap-2 text-sm">
                      <Link
                        href={withBack(`/orders/${row.id}`, BACK)}
                        className="shrink-0 font-semibold tabular-nums underline-offset-2 hover:underline"
                      >
                        №{row.id}
                      </Link>
                      <span className="min-w-0 truncate font-medium">
                        {iso && (
                          <span aria-hidden className="mr-1">
                            {countryFlag(iso)}
                          </span>
                        )}
                        {row.client_name}
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs tabular-nums text-[var(--muted-foreground)]">
                      {row.planned_on === today ? "Сегодня" : formatIsoDayMonth(row.planned_on)} · {row.bags} меш.
                    </div>
                  </div>
                  {row.transport_locked ? (
                    <div className="flex flex-1 flex-wrap items-center gap-2 text-sm">
                      <PlatePair truck={row.truck_number} trailer={row.trailer_number} />
                      <span className="flex items-center gap-1 text-xs text-[var(--muted-foreground)]">
                        <Lock className="size-3.5" /> Номер указал клиент
                      </span>
                    </div>
                  ) : (
                    <div className="grid flex-1 gap-2 sm:grid-cols-2">
                      <div className="grid gap-1">
                        <span className={cn(COLUMN_LABEL, "lg:hidden")}>Тягач</span>
                        <PlateInput
                          ref={register(row.id, "truck")}
                          aria-label={`Тягач №${row.id}`}
                          enterKeyHint="next"
                          defaultCountry={row.client_country}
                          value={numbers.truck_number}
                          onChange={(truck_number) => edit(row, { truck_number })}
                          onKeyDown={keyDown(row, "truck")}
                        />
                      </div>
                      <div className="grid gap-1">
                        <span className={cn(COLUMN_LABEL, "lg:hidden")}>Прицеп (необязательно)</span>
                        <PlateInput
                          ref={register(row.id, "trailer")}
                          aria-label={`Прицеп №${row.id}`}
                          kind="trailer"
                          enterKeyHint="done"
                          defaultCountry={row.client_country}
                          value={numbers.trailer_number}
                          onChange={(trailer_number) => edit(row, { trailer_number })}
                          onKeyDown={keyDown(row, "trailer")}
                        />
                      </div>
                    </div>
                  )}
                </div>
                <PlateSuggestions suggestions={suggestions} current={numbers} onPick={(pair) => choose(row, pair)} />
                <RowState status={statuses[row.id]} dirty={dirty} warning={row.plate_warning} />
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
