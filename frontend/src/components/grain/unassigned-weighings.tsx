"use client";

import { useState } from "react";
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

const CANDIDATES_URL = "/grain/wagons/?scope=on_site&direction=passage";
/** Больше этого числа строк панель сворачивает: оператору важны последние. */
const COLLAPSED_ROWS = 3;
/** Пустая машина весит около 4 т, гружёная 8–11 т: граница для подсказки. */
const LOADED_THRESHOLD_KG = 6_000;

/** Панель работает поверх общего useApi; чужой или битый ответ просто не показывается. */
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
 * чтобы самый вероятный стоял первым, но выбор остаётся за оператором.
 */
function rankCandidates(item: GrainUnassignedWeighing, candidates: GrainWagon[]) {
  const loaded = item.weight_kg >= LOADED_THRESHOLD_KG;
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
}: {
  item: GrainUnassignedWeighing;
  candidates: GrainWagon[];
  canWeigh: boolean;
  onResolved: () => void;
}) {
  const ranked = rankCandidates(item, candidates);
  const loaded = item.weight_kg >= LOADED_THRESHOLD_KG;
  const [mode, setMode] = useState<"idle" | "assign" | "create" | "discard">("idle");
  const [wagonId, setWagonId] = useState(() => (ranked[0] && loaded ? String(ranked[0].id) : ""));
  const [number, setNumber] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const photo = apiFileUrl(item.photo_url);

  async function run(path: string, body: Record<string, unknown>) {
    setBusy(true);
    setError("");
    try {
      await api.post(`/grain/unassigned-weighings/${item.id}/${path}/`, body);
      setMode("idle");
      onResolved();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="border-b border-[var(--border)]/70 last:border-0">
      <div className="flex items-center gap-3 px-3 py-2">
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
            <Camera className="size-4" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="text-base font-semibold tabular-nums">{formatKg(item.weight_kg)}</span>
            <span className="text-xs text-[var(--muted-foreground)]">{loaded ? "гружёная" : "пустая"}</span>
            <span className="text-xs text-[var(--muted-foreground)]">· {formatDateTime(item.stable_weight_at)}</span>
          </div>
          <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
            {loaded
              ? ranked[0] && awaitsExit(ranked[0])
                ? `похоже на выезд ${ranked[0].number || `#${ranked[0].id}`}`
                : "номер не распознан, похоже на выезд"
              : "номер не распознан, похоже на новый заезд"}
          </div>
        </div>
        {canWeigh && mode === "idle" && (
          <div className="flex shrink-0 items-center gap-1.5">
            <Button size="sm" variant={loaded ? "default" : "outline"} onClick={() => setMode("assign")}>
              <Scale /> Привязать
            </Button>
            <Button size="sm" variant={loaded ? "outline" : "default"} onClick={() => setMode("create")}>
              <PackagePlus /> Новый рейс
            </Button>
            <Button
              size="sm"
              variant="ghost"
              aria-label="Отклонить взвешивание"
              title="Отклонить"
              onClick={() => setMode("discard")}
            >
              <Trash2 />
            </Button>
          </div>
        )}
      </div>

      {mode !== "idle" && (
        <div className="px-3 pb-3 pl-[7.5rem]">
          {mode === "assign" && (
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                void run("assign", { wagon: Number(wagonId) });
              }}
            >
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
              <Button size="sm" type="submit" disabled={busy || !wagonId}>
                {busy ? <LoaderCircle className="animate-spin" /> : <Check />} Привязать
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
}: {
  canWeigh: boolean;
  active?: boolean;
  onChanged?: () => void;
}) {
  const { data, reload } = useApi<GrainUnassignedWeighing[]>("/grain/unassigned-weighings/");
  const { data: candidatesData, reload: reloadCandidates } = useApi<GrainWagon[] | { results: GrainWagon[] }>(
    CANDIDATES_URL,
  );
  const [expanded, setExpanded] = useState(false);
  useVisiblePolling(reload, 10_000, active);
  const items = Array.isArray(data) ? data.filter(isUnassignedWeighing) : [];
  const rawCandidates = Array.isArray(candidatesData) ? candidatesData : (candidatesData?.results ?? []);
  const candidates = Array.isArray(rawCandidates) ? rawCandidates.filter(isWagon) : [];
  if (!items.length) return null;
  const visible = expanded ? items : items.slice(0, COLLAPSED_ROWS);
  const hidden = items.length - visible.length;

  return (
    <section
      aria-label="Неопознанные взвешивания"
      className="overflow-hidden rounded-xl border border-amber-200 bg-[var(--card)]"
    >
      <header className="flex flex-wrap items-center gap-2 border-b border-amber-200 bg-amber-50/70 px-3 py-2">
        <Scale className="size-4 text-amber-700" />
        <span className="text-sm font-semibold">Неопознанные взвешивания</span>
        <Badge tone="warning">{items.length}</Badge>
        <span className="text-xs text-[var(--muted-foreground)]">
          вес и фото сохранены, номер не прочитался · привяжите к рейсу или создайте новый
        </span>
      </header>
      <ul>
        {visible.map((item) => (
          <UnassignedRow
            key={item.id}
            item={item}
            candidates={candidates}
            canWeigh={canWeigh}
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
