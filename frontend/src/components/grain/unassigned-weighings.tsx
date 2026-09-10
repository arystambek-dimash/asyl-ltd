"use client";

import { useRef, useState } from "react";
import { HistoricalTareDialog } from "./historical-tare-dialog";
import { ManualPassageEntryDialog } from "./manual-passage-entry-dialog";
import { photoStatusLabel, weighingReasonLabel, identityReviewLabel } from "@/lib/weighing-evidence";
import { Camera, Check, ChevronDown, LoaderCircle, PackagePlus, Scale, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { apiFileUrl, formatKg } from "@/lib/grain";
import type { GrainUnassignedWeighing, GrainWagon } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn, formatDateTime } from "@/lib/utils";

const CANDIDATES_URL = "/grain/passages/?scope=on_site";
/** Больше этого числа строк панель сворачивает: оператору важны последние. */
const COLLAPSED_ROWS = 3;
/** Пустая машина весит около 4 т, гружёная 8–11 т: граница для подсказки без камеры. */
const LOADED_THRESHOLD_KG = 6_000;

/**
 * Камера весовой смотрит вдоль весов: машина передом — заезжает, задом —
 * выезжает. Это главный признак; вес — запасной, когда камера не ответила.
 */
function looksLoaded(item: GrainUnassignedWeighing) {
  if (item.orientation === "rear") return true;
  if (item.orientation === "front") return false;
  return item.weight_kg >= LOADED_THRESHOLD_KG;
}

function orientationHint(item: GrainUnassignedWeighing) {
  if (item.orientation === "rear") return "камера: задом → выезд";
  if (item.orientation === "front") return "камера: передом → заезд";
  return "";
}

/** Only an active backend attempt belongs to the processing queue. */
function isProcessing(item: GrainUnassignedWeighing) {
  const status = item.identity_check?.status;
  // A terminal capture failure cannot recover by waiting for a different truck's photo.
  if (status === "waiting_photo" && item.photo_status === "unavailable") return false;
  return Boolean(status && ["pending", "processing", "retrying", "waiting_photo", "matched"].includes(status));
}

function identityStatusLabel(item: GrainUnassignedWeighing) {
  const check = item.identity_check;
  if (!check) return "";
  if (check.status === "waiting_photo") {
    return item.photo_status === "unavailable"
      ? identityReviewLabel("photo_unavailable", item.orientation)
      : "Сохраняем кадр этого взвешивания для распознавания номера…";
  }
  if (check.status === "waiting_budget") return "Лимит ИИ на сегодня исчерпан — нужна резервная ручная проверка";
  if (check.status === "disabled") return "Проверка ИИ отключена — нужна резервная ручная проверка";
  if (check.status === "review") return identityReviewLabel(check.review_reason || check.reason, item.orientation);
  if (check.status === "matched") return "Номер подтверждён — обновляем рейс…";
  if (check.status === "retrying") {
    if (check.reason === "image_binding_recheck") return "ИИ повторно сверяет только фото этой машины…";
    if (check.reason === "entry_evidence_pending" && item.orientation !== "front") {
      return "Повторно проверяем номер, открытые рейсы и сохранённую тару…";
    }
    return "Повторяем автоматическое распознавание сохранённого кадра…";
  }
  return "Распознаём госномер и направление проезда…";
}

/** Validate the queue before offering physical weighing actions. */
function isUnassignedWeighing(value: unknown): value is GrainUnassignedWeighing {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<GrainUnassignedWeighing>;
  return (
    typeof item.id === "number" &&
    typeof item.weight_kg === "number" &&
    typeof item.stable_weight_at === "string" &&
    !Number.isNaN(new Date(item.stable_weight_at).getTime())
  );
}

function isWagon(value: unknown): value is GrainWagon {
  return Boolean(value) && typeof value === "object" && typeof (value as GrainWagon).id === "number";
}

function awaitsEntry(wagon: GrainWagon) {
  return wagon.status === "arrived" && wagon.entry_weight_kg == null;
}

function awaitsExit(wagon: GrainWagon) {
  return wagon.status === "at_silo" && wagon.exit_weight_kg == null;
}

function canBeExit(item: GrainUnassignedWeighing, wagon: GrainWagon) {
  // Passage entry time; a repaired weighing record may have been created later.
  const entryAt = wagon.silo_arrived_at || wagon.arrived_at;
  return (
    wagon.direction === "passage" &&
    awaitsExit(wagon) &&
    wagon.entry_weight_kg != null &&
    item.weight_kg > wagon.entry_weight_kg &&
    item.orientation !== "front" &&
    (!entryAt || new Date(item.stable_weight_at).getTime() > new Date(entryAt).getTime())
  );
}

function candidateLabel(wagon: GrainWagon) {
  const stage = awaitsEntry(wagon)
    ? "ждёт вес пустой"
    : awaitsExit(wagon)
      ? `ждёт вес гружёной · заехала ${formatKg(wagon.entry_weight_kg)}`
      : wagon.status_label;
  return `${wagon.number || `#${wagon.id}`} · ${stage}`;
}

/**
 * Подсказка по весу: гружёная машина почти наверняка выезд одной из тех,
 * что ждут вес гружёной; пустая — новый заезд. Кандидаты сортируются так,
 * чтобы подходящий этап стоял первым. Вес не определяет номер машины.
 */
function rankCandidates(item: GrainUnassignedWeighing, candidates: GrainWagon[]) {
  const loaded = looksLoaded(item);
  const waiting = candidates.filter((wagon) => awaitsEntry(wagon) || awaitsExit(wagon));
  return waiting.sort((a, b) => {
    const score = (wagon: GrainWagon) => {
      if (loaded && awaitsExit(wagon)) {
        const entry = wagon.entry_weight_kg ?? 0;
        return entry < item.weight_kg ? 2 : 1;
      }
      if (!loaded && awaitsEntry(wagon)) return 2;
      return 0;
    };
    return score(b) - score(a);
  });
}

function UnassignedRow({
  item,
  candidates,
  canWeigh,
  onResolved,
  exitWagon,
  onBusyChange,
}: {
  item: GrainUnassignedWeighing;
  candidates: GrainWagon[];
  canWeigh: boolean;
  onResolved: () => void;
  exitWagon?: GrainWagon;
  onBusyChange?: (busy: boolean) => void;
}) {
  const ranked = rankCandidates(item, candidates);
  const loaded = Boolean(exitWagon) || looksLoaded(item);
  const exits = ranked.filter((wagon) => canBeExit(item, wagon));
  const suggestedExit = loaded && exits.length === 1 ? exits[0] : undefined;
  const cameraHint = orientationHint(item);
  const [mode, setMode] = useState<"idle" | "assign" | "create" | "discard">("idle");
  const [wagonId, setWagonId] = useState("");
  const [number, setNumber] = useState(item.vehicle_number ?? "");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const photo = apiFileUrl(item.photo_url);
  const processing = isProcessing(item);
  const identityLabel = identityStatusLabel(item);

  async function run(path: string, body: Record<string, unknown>) {
    if (busy || !canWeigh || processing) return;
    setBusy(true);
    onBusyChange?.(true);
    setError("");
    try {
      await api.post(`/grain/unassigned-weighings/${item.id}/${path}/`, body);
      setMode("idle");
      onResolved();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
      onBusyChange?.(false);
    }
  }

  return (
    <li className="border-b border-[var(--border)]/70 last:border-0">
      <div className="flex flex-wrap items-center gap-3 px-3 py-2">
        {photo ? (
          <a
            href={photo}
            target="_blank"
            rel="noreferrer"
            className="block h-14 w-24 shrink-0 overflow-hidden rounded-md border bg-black/5"
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- подписанная ссылка бэкенда */}
            <img src={photo} alt="Машина на весах" loading="lazy" className="size-full object-cover" />
          </a>
        ) : (
          <div className="flex h-14 w-24 shrink-0 items-center justify-center rounded-md border border-dashed text-[var(--muted-foreground)]">
            <span className="px-1 text-center text-[10px]">
              <Camera className="mx-auto size-4" />
              {photoStatusLabel(item.photo_status)}
            </span>
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="text-base font-semibold tabular-nums">{formatKg(item.weight_kg)}</span>
            {item.vehicle_number && <span className="font-mono text-sm font-semibold">{item.vehicle_number}</span>}
            <span className="text-xs text-[var(--muted-foreground)]">
              {loaded ? "возможный выезд" : "возможный заезд"}
            </span>
            <span className="text-xs text-[var(--muted-foreground)]">· {formatDateTime(item.stable_weight_at)}</span>
          </div>
          {identityLabel && (
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {identityLabel}
              {item.identity_check?.plate &&
                item.identity_check.plate !== item.vehicle_number &&
                ` · вариант ИИ: ${item.identity_check.plate}`}
            </p>
          )}
          <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            {processing
              ? "Вес сохранён · действия оператора не нужны"
              : exitWagon
                ? `Сверьте фото с машиной ${exitWagon.number || `#${exitWagon.id}`}`
                : item.reason && item.reason !== "open_passages_exist"
                  ? weighingReasonLabel(item.reason)
                  : loaded
                    ? suggestedExit
                      ? `похоже на выезд ${suggestedExit.number || `#${suggestedExit.id}`}`
                      : "номер не распознан — выберите рейс по фото и времени"
                    : "номер не распознан, похоже на новый заезд"}
            {cameraHint && <span className="ml-1 text-amber-700">· {cameraHint}</span>}
          </div>
        </div>
        {processing && (
          <LoaderCircle aria-label="Обработка взвешивания" className="size-4 animate-spin text-[var(--ring)]" />
        )}
        {canWeigh && !processing && mode === "idle" && (
          <div className="flex shrink-0 items-center gap-1.5">
            <Button
              size="sm"
              variant={loaded || exitWagon ? "default" : "outline"}
              onClick={() => {
                setWagonId(String(exitWagon?.id || suggestedExit?.id || ""));
                setMode("assign");
              }}
            >
              <Scale /> {exitWagon ? "Выбрать этот вес" : "Привязать"}
            </Button>
            {!exitWagon && (
              <>
                {item.orientation !== "rear" && (
                  <Button size="sm" variant={loaded ? "outline" : "default"} onClick={() => setMode("create")}>
                    <PackagePlus /> Новый рейс
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  aria-label="Отклонить взвешивание"
                  title="Отклонить"
                  onClick={() => setMode("discard")}
                >
                  <Trash2 />
                </Button>
              </>
            )}
          </div>
        )}
      </div>

      {!processing && !exitWagon && item.orientation !== "front" && (
        <div className="flex flex-wrap gap-2 px-3 pb-2">
          <HistoricalTareDialog
            disabled={!canWeigh || mode !== "idle"}
            item={item}
            onChanged={onResolved}
            onBusyChange={onBusyChange}
          />
          <ManualPassageEntryDialog
            disabled={mode !== "idle" || busy}
            item={item}
            onChanged={onResolved}
            onBusyChange={onBusyChange}
          />
        </div>
      )}

      {!processing && mode !== "idle" && (
        <div className="px-3 pb-3 sm:pl-[7.5rem]">
          {mode === "assign" && (
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (canWeigh && wagonId) void run("assign", { wagon: Number(wagonId) });
              }}
            >
              {exitWagon ? (
                <p className="w-full text-sm">
                  Вывоз {exitWagon.number || `#${exitWagon.id}`}: вес гружёной {formatKg(item.weight_kg)}, нетто{" "}
                  {formatKg(item.weight_kg - (exitWagon.entry_weight_kg ?? 0))}. Записать этот вес и завершить рейс?
                </p>
              ) : (
                <Select
                  aria-label="Рейс для привязки"
                  value={wagonId}
                  onChange={(event) => setWagonId(event.target.value)}
                  className="h-9 min-w-64"
                >
                  <option value="">Выберите рейс…</option>
                  {ranked.map((wagon) => (
                    <option key={wagon.id} value={wagon.id}>
                      {candidateLabel(wagon)}
                    </option>
                  ))}
                </Select>
              )}
              <Button size="sm" type="submit" disabled={busy || !wagonId || !canWeigh}>
                {busy ? <LoaderCircle className="animate-spin" /> : <Check />}
                {exitWagon ? "Записать выезд и завершить рейс" : "Привязать"}
              </Button>
              <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setMode("idle")}>
                Отмена
              </Button>
            </form>
          )}

          {mode === "create" && (
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void run("create-passage", { number, cargo_name: "" });
              }}
            >
              <Input
                aria-label="Номер машины"
                value={number}
                onChange={(event) => setNumber(event.target.value.toUpperCase())}
                placeholder="Номер (можно пустой)"
                className="h-9 w-48 font-mono uppercase"
                maxLength={30}
              />
              <Button size="sm" type="submit" disabled={busy}>
                {busy ? <LoaderCircle className="animate-spin" /> : <PackagePlus />} Создать и записать заезд
              </Button>
              <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setMode("idle")}>
                Отмена
              </Button>
            </form>
          )}

          {mode === "discard" && (
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void run("discard", { reason });
              }}
            >
              <Input
                aria-label="Причина отклонения"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="Причина (необязательно)"
                className="h-9 w-64"
                maxLength={200}
              />
              <Button size="sm" type="submit" variant="destructive" disabled={busy}>
                {busy ? <LoaderCircle className="animate-spin" /> : <Trash2 />} Отклонить
              </Button>
              <Button size="sm" type="button" variant="ghost" disabled={busy} onClick={() => setMode("idle")}>
                Отмена
              </Button>
            </form>
          )}

          {error && (
            <p role="alert" className="mt-2 text-xs text-[var(--destructive)]">
              {error}
            </p>
          )}
        </div>
      )}
    </li>
  );
}

function WeighingQueue({
  items,
  processing,
  candidates,
  canWeigh,
  exitWagon,
  onBusyChange,
  onResolved,
  loading,
  error,
  refresh,
}: {
  items: GrainUnassignedWeighing[];
  processing: boolean;
  candidates: GrainWagon[];
  canWeigh: boolean;
  exitWagon?: GrainWagon;
  onBusyChange?: (busy: boolean) => void;
  onResolved: () => void;
  loading?: boolean;
  error?: string;
  refresh: () => Promise<unknown>;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, COLLAPSED_ROWS);
  const hidden = items.length - visible.length;
  const title = processing
    ? "Автоматическая обработка"
    : exitWagon
      ? "Выезд без распознанного номера"
      : "Неопознанные взвешивания";
  return (
    <section
      aria-label={title}
      className={cn(
        "overflow-hidden rounded-xl border bg-[var(--card)]",
        processing ? "border-[var(--ring)]/25" : "border-amber-200",
      )}
    >
      <header
        className={cn(
          "flex flex-wrap items-center gap-2 border-b px-3 py-2",
          processing ? "border-[var(--ring)]/25 bg-[var(--ring)]/5" : "border-amber-200 bg-amber-50/70",
        )}
      >
        {processing ? (
          <LoaderCircle className="size-4 animate-spin text-[var(--ring)]" />
        ) : (
          <Scale className="size-4 text-amber-700" />
        )}
        <span className="text-sm font-semibold">{title}</span>
        <Badge tone={processing ? "primary" : "warning"}>{items.length}</Badge>
        <span className="text-xs text-[var(--muted-foreground)]">
          {processing
            ? "Вес сохранён · система распознаёт машину и оформляет рейс"
            : "Автоматическая обработка не завершилась · проверьте сохранённое взвешивание"}
        </span>
      </header>
      {loading && (
        <p role="status" className="p-3 text-sm">
          Загружаем взвешивания…
        </p>
      )}
      {error && (
        <div role="alert" className="p-3 text-sm">
          <p>{error}</p>
          <Button size="sm" variant="outline" className="mt-2" onClick={() => void refresh()}>
            Повторить загрузку
          </Button>
        </div>
      )}
      <ul>
        {visible.map((item) => (
          <UnassignedRow
            key={item.id}
            item={item}
            candidates={candidates}
            canWeigh={canWeigh}
            exitWagon={exitWagon}
            onBusyChange={onBusyChange}
            onResolved={onResolved}
          />
        ))}
      </ul>
      {(hidden > 0 || expanded) && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="flex w-full items-center justify-center gap-1 border-t border-[var(--border)]/70 px-3 py-2 text-xs font-medium text-[var(--muted-foreground)] hover:bg-[var(--muted)]/60"
        >
          <ChevronDown className={cn("size-3.5 transition-transform", expanded && "rotate-180")} />
          {expanded ? "Свернуть" : `Показать ещё ${hidden}`}
        </button>
      )}
    </section>
  );
}

/** Active attempts stay visible separately from terminal exceptions requiring review. */
export function UnassignedWeighingsPanel({
  canWeigh,
  active = true,
  onChanged,
  exitWagon,
  onBusyChange,
}: {
  canWeigh: boolean;
  active?: boolean;
  onChanged?: () => void;
  exitWagon?: GrainWagon;
  onBusyChange?: (busy: boolean) => void;
}) {
  const [mutationBusy, setMutationBusy] = useState(false);
  const mutationBusyRef = useRef(false);
  const { data, reload, loading, error, setData } = useApi<GrainUnassignedWeighing[]>("/grain/unassigned-weighings/");
  const {
    data: candidatesData,
    reload: reloadCandidates,
    error: candidatesError,
    setData: setCandidatesData,
  } = useApi<GrainWagon[] | { results: GrainWagon[] }>(exitWagon ? null : CANDIDATES_URL);
  const refresh = () => (mutationBusyRef.current ? Promise.resolve([]) : Promise.all([reload(), reloadCandidates()]));
  useVisiblePolling(refresh, 10_000, active && !mutationBusy);

  function handleBusyChange(busy: boolean) {
    mutationBusyRef.current = busy;
    if (busy) {
      // Cancel reads already in flight before opening a dialog or submitting
      // an action. The selected weighing must not disappear under the form.
      setData(data);
      setCandidatesData(candidatesData);
    }
    setMutationBusy(busy);
    onBusyChange?.(busy);
    if (!busy) void refresh();
  }
  const invalidQueue = data !== null && (!Array.isArray(data) || !data.every(isUnassignedWeighing));
  const loadError = error || (invalidQueue ? "Сервер вернул некорректный список взвешиваний." : "");
  const items = Array.isArray(data)
    ? data.filter(isUnassignedWeighing).filter((item) => !exitWagon || canBeExit(item, exitWagon))
    : [];
  const rawCandidates = Array.isArray(candidatesData) ? candidatesData : (candidatesData?.results ?? []);
  const candidates = exitWagon ? [exitWagon] : Array.isArray(rawCandidates) ? rawCandidates.filter(isWagon) : [];
  if (!items.length && !loading && !loadError) return null;
  const processing = items.filter(isProcessing);
  const exceptions = items.filter((item) => !isProcessing(item));
  const shared = {
    candidates,
    canWeigh: canWeigh && !loadError && !candidatesError,
    exitWagon,
    onBusyChange: handleBusyChange,
    refresh,
    onResolved: () => onChanged?.(),
  };

  return (
    <div className="space-y-3">
      {processing.length > 0 && <WeighingQueue {...shared} processing items={processing} />}
      {(exceptions.length > 0 || loadError || (loading && !data)) && (
        <WeighingQueue
          {...shared}
          processing={false}
          items={exceptions}
          loading={loading && !data}
          error={loadError || (candidatesError ? `Не удалось обновить рейсы для привязки: ${candidatesError}` : "")}
        />
      )}
    </div>
  );
}
