"use client";

import { useState } from "react";
import { HistoricalTareDialog } from "./historical-tare-dialog";
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

  async function run(path: string, body: Record<string, unknown>) {
    if (busy || !canWeigh) return;
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
          {item.identity_check && item.identity_check.status !== "disabled" && (
            <p className="mt-1 text-xs text-[var(--muted-foreground)]">
              {item.identity_check.status === "waiting_photo"
                ? "Ожидаем фото для ИИ — вес сохранён, доступна ручная привязка"
                : item.identity_check.status === "waiting_budget"
                  ? "Лимит ИИ на сегодня исчерпан — доступна ручная привязка"
                  : item.identity_check.status === "review"
                    ? identityReviewLabel(item.identity_check.review_reason)
                    : item.identity_check.status === "matched"
                      ? "ИИ: номер и машина совпали"
                      : item.identity_check.status === "retrying"
                        ? item.identity_check.reason === "image_binding_recheck"
                          ? "ИИ повторно сверяет только фото этой машины"
                          : item.identity_check.reason === "entry_evidence_pending"
                            ? "Ожидаем фото заезда для проверки ИИ"
                            : "ИИ временно недоступен, повторим проверку"
                        : "ИИ сверяет номер и машину с фото заезда…"}
              {item.identity_check.plate && ` · вариант ИИ: ${item.identity_check.plate}`}
            </p>
          )}
          <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            {exitWagon
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
        {canWeigh && mode === "idle" && (
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

      {!exitWagon && item.orientation !== "front" && (
        <div className="px-3 pb-2">
          <HistoricalTareDialog
            disabled={!canWeigh || mode !== "idle"}
            item={item}
            onChanged={onResolved}
            onBusyChange={onBusyChange}
          />
        </div>
      )}

      {mode !== "idle" && (
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

/**
 * Веса автовесов, которые не удалось привязать без оператора. Панель сама
 * исчезает, когда очередь пуста, обновляется тем же ритмом, что таблица, и
 * показывает только последние строки, пока оператор не развернёт список.
 */
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
  const { data, reload, loading, error } = useApi<GrainUnassignedWeighing[]>("/grain/unassigned-weighings/");
  const {
    data: candidatesData,
    reload: reloadCandidates,
    error: candidatesError,
  } = useApi<GrainWagon[] | { results: GrainWagon[] }>(exitWagon ? null : CANDIDATES_URL);
  const [expanded, setExpanded] = useState(false);
  const refresh = () => Promise.all([reload(), reloadCandidates()]);
  useVisiblePolling(refresh, 10_000, active);
  const invalidQueue = data !== null && (!Array.isArray(data) || !data.every(isUnassignedWeighing));
  const loadError = error || (invalidQueue ? "Сервер вернул некорректный список взвешиваний." : "");
  const items = Array.isArray(data)
    ? data.filter(isUnassignedWeighing).filter((item) => !exitWagon || canBeExit(item, exitWagon))
    : [];
  const rawCandidates = Array.isArray(candidatesData) ? candidatesData : (candidatesData?.results ?? []);
  const candidates = exitWagon ? [exitWagon] : Array.isArray(rawCandidates) ? rawCandidates.filter(isWagon) : [];
  if (!items.length && !loading && !loadError) return null;
  const visible = expanded ? items : items.slice(0, COLLAPSED_ROWS);
  const hidden = items.length - visible.length;

  return (
    <section
      aria-label={exitWagon ? "Выезд без распознанного номера" : "Неопознанные взвешивания"}
      className="overflow-hidden rounded-xl border border-amber-200 bg-[var(--card)]"
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50/70 px-3 py-2">
        <Scale className="size-4 text-amber-700" />
        <span className="text-sm font-semibold">
          {exitWagon ? "Выезд без распознанного номера" : "Неопознанные взвешивания"}
        </span>
        <Badge tone="warning">{items.length}</Badge>
        <span className="text-xs text-[var(--muted-foreground)]">
          {exitWagon
            ? "Выберите взвешивание этой машины по фото и времени. Повторное распознавание номера не требуется."
            : "Вес сохранён без привязки · выберите рейс по фото и времени или создайте новый"}
        </span>
      </header>
      {loading && !data && (
        <p role="status" className="p-3 text-sm">
          Загружаем неопознанные взвешивания…
        </p>
      )}
      {(loadError || candidatesError) && (
        <div role="alert" className="p-3 text-sm">
          <p>{loadError || `Не удалось обновить рейсы для привязки: ${candidatesError}`}</p>
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
            canWeigh={canWeigh && !loadError && !candidatesError}
            exitWagon={exitWagon}
            onBusyChange={onBusyChange}
            onResolved={() => {
              void reload();
              void reloadCandidates();
              onChanged?.();
            }}
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
