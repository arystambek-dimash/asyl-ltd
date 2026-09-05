"use client";

import { useCallback, useId, useMemo, useRef, useState } from "react";
import { Ban, Camera, Cpu, Images, LoaderCircle, Radio, RefreshCw, Trash2, Undo2 } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { NoAccessCard } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadMore } from "@/components/ui/load-more";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { api, apiError } from "@/lib/api";
import { apiFileUrl, formatKg } from "@/lib/grain";
import type {
  GrainOrientationCameraPc,
  GrainOrientationLabel,
  GrainOrientationPurgeResult,
  GrainOrientationSample,
  GrainOrientationSummary,
  GrainOrientationTrainingReport,
  Me,
  VehicleOrientation,
} from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const PAGE_TITLE = "Датасет ориентации";
const LIST_URL = "/grain/orientation-samples/";
const SUMMARY_URL = "/grain/orientation-samples/summary/";
const PURGE_URL = "/grain/orientation-samples/purge/";
/** Обученной модели старые кадры не нужны; месяц — запас на разбор конфликтов. */
const DEFAULT_PURGE_DAYS = 30;
/** Датасет живёт 60 дней (VEHICLE_ORIENTATION_SAMPLE_MAX_AGE_DAYS); 10 лет — заведомо «ничего». */
const MAX_PURGE_DAYS = 3650;
/** Бэкенд удаляет пакетами; предел на случай, если ПК всё время отдаёт remaining > 0. */
const MAX_PURGE_BATCHES = 100;
/** Кратно ряду сетки (2–4 карточки), чтобы страница заканчивалась полным рядом. */
const PAGE_SIZE = 48;
/** Датасет пополняется ночным экспортом и редкими правками: частый опрос не нужен. */
/** Подписанная ссылка на фото живёт час; обновляем её заранее. */
const PHOTO_LINK_REFRESH_MS = 45 * 60_000;
const POLL_INTERVAL_MS = 30_000;

type Tone = "muted" | "primary" | "success" | "warning" | "destructive" | "outline";

const SCOPES = ["all", "conflict", "weight", "trip", "manual", "excluded", "unsent"] as const;
type Scope = (typeof SCOPES)[number];

const LABEL_FILTERS = ["all", "front", "rear"] as const;
type LabelFilter = (typeof LABEL_FILTERS)[number];

const PURGE_MODES = ["older", "all"] as const;
type PurgeMode = (typeof PURGE_MODES)[number];

/** Как вкладка фильтра ложится в параметры списка. */
const SCOPE_QUERY: Record<Scope, Record<string, string>> = {
  all: {},
  conflict: { conflict: "1" },
  weight: { source: "weight" },
  trip: { source: "trip" },
  manual: { source: "manual" },
  excluded: { excluded: "1" },
  unsent: { unsent: "1" },
};

const SCOPE_LABELS: Record<Scope, string> = {
  all: "Все",
  conflict: "Конфликты",
  weight: "По весу",
  trip: "По рейсу",
  manual: "Вручную",
  excluded: "Исключённые",
  unsent: "Не отправлены",
};

const LABEL_META: Record<GrainOrientationLabel, { badge: string; button: string; word: string; tone: Tone }> = {
  front: { badge: "Передом → заезд", button: "Передом", word: "передом", tone: "success" },
  rear: { badge: "Задом → выезд", button: "Задом", word: "задом", tone: "primary" },
};

const SOURCE_LABELS: Record<GrainOrientationSample["label_source"], string> = {
  trip: "по рейсу",
  weight: "по весу",
  manual: "вручную",
};

/** Статусы ночного обучения задаёт Camera-PC; незнакомый показываем как есть. */
const TRAINING_STATUS: Record<string, { label: string; tone: Tone }> = {
  promoted: { label: "промотирована", tone: "success" },
  kept_current: { label: "оставлена текущая", tone: "muted" },
  kept: { label: "оставлена текущая", tone: "muted" },
  rejected: { label: "оставлена текущая", tone: "muted" },
  not_promoted: { label: "оставлена текущая", tone: "muted" },
  skipped: { label: "пропущено", tone: "muted" },
  error: { label: "ошибка", tone: "destructive" },
  failed: { label: "ошибка", tone: "destructive" },
};

function isScope(value: string): value is Scope {
  return (SCOPES as readonly string[]).includes(value);
}

function isLabelFilter(value: string): value is LabelFilter {
  return (LABEL_FILTERS as readonly string[]).includes(value);
}

function isPurgeMode(value: string): value is PurgeMode {
  return (PURGE_MODES as readonly string[]).includes(value);
}

/** Срок в днях из поля ввода; null — пусто или не целое число от 1 до MAX_PURGE_DAYS. */
function purgeDays(value: string): number | null {
  const days = Number(value);
  return value.trim() !== "" && Number.isInteger(days) && days >= 1 && days <= MAX_PURGE_DAYS ? days : null;
}

function scopeCount(summary: GrainOrientationSummary | null, scope: Scope): number | undefined {
  if (!summary) return undefined;
  switch (scope) {
    case "all":
      return summary.total;
    case "conflict":
      return summary.conflicts;
    case "excluded":
      return summary.excluded;
    case "unsent":
      return summary.unsent;
    default:
      return summary.by_source[scope];
  }
}

function modelBadge(orientation: VehicleOrientation): string {
  return orientation ? `модель: ${LABEL_META[orientation].word}` : "модель не согласна";
}

function trainingStatusMeta(training: GrainOrientationTrainingReport): { label: string; tone: Tone } {
  if (training.promoted) return TRAINING_STATUS.promoted;
  return TRAINING_STATUS[training.status?.toLowerCase()] ?? { label: training.status || "нет данных", tone: "outline" };
}

function formatAccuracy(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const percent = value <= 1 ? value * 100 : value;
  return `${percent.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`;
}

/** ПК может отдать число кадров или разбивку по классам. */
function formatSamples(value: GrainOrientationTrainingReport["samples"]): string {
  if (typeof value === "number") return value.toLocaleString("ru-RU");
  if (!value || typeof value !== "object") return "—";
  const total = typeof value.total === "number" ? value.total : Object.values(value).reduce((sum, n) => sum + n, 0);
  return total.toLocaleString("ru-RU");
}

/**
 * Дообученная модель на ПК лежит в `vehicle-orientation.trained.pt` и после промоции
 * становится текущей; ПК описывает модель произвольным объектом, поэтому смотрим и на
 * флаги, и на имя файла.
 */
function selfTrainedActive(pc: GrainOrientationCameraPc): boolean {
  const model = pc.model ?? {};
  if (model.trained === true || model.self_trained === true) return true;
  const names = [pc.training?.current_model, model.name, model.path, model.file, model.source];
  return names.some((name) => typeof name === "string" && /trained/i.test(name));
}

/**
 * Ответ POST — свежее любого GET, начатого до него. Опрос, завершившийся после
 * правки, вернул бы старую метку; сравниваем по reviewed_at, который ставит правка.
 */
function freshest(row: GrainOrientationSample, override: GrainOrientationSample | undefined): GrainOrientationSample {
  if (!override) return row;
  const rowAt = Date.parse(row.reviewed_at ?? "") || 0;
  const overrideAt = Date.parse(override.reviewed_at ?? "") || 0;
  // Сервер, догнавший правку, важнее: он несёт sent_at и last_error ночного экспорта.
  return overrideAt > rowAt ? override : row;
}

function Stat({ label, value, warn = false }: { label: string; value: number; warn?: boolean }) {
  return (
    <div className="rounded-lg border border-[var(--border)] px-3 py-2">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-[var(--muted-foreground)]">{label}</dt>
      <dd className={cn("text-xl font-semibold tabular-nums", warn && value > 0 && "text-[var(--warning)]")}>
        {value.toLocaleString("ru-RU")}
      </dd>
    </div>
  );
}

function CameraPcBlock({ pc }: { pc: GrainOrientationCameraPc | null }) {
  if (!pc) {
    return (
      <section aria-label="ПК камер" className="rounded-lg border border-dashed border-[var(--border)] p-3 text-sm">
        <div className="mb-1 flex items-center gap-2 font-semibold">
          <Cpu className="size-4 text-[var(--muted-foreground)]" /> ПК камер
        </div>
        <p className="text-[var(--muted-foreground)]">ПК камер недоступен</p>
      </section>
    );
  }
  const training = pc.training ?? null;
  const status = training ? trainingStatusMeta(training) : null;
  return (
    <section
      aria-label="ПК камер"
      className="rounded-lg border border-[var(--border)] bg-[var(--muted)]/40 p-3 text-sm"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Cpu className="size-4 text-[var(--muted-foreground)]" />
        <span className="font-semibold">ПК камер</span>
        {pc.enabled === false && <Badge tone="muted">датасет выключен</Badge>}
        {selfTrainedActive(pc) ? (
          <Badge tone="success" dot>
            самообученная модель активна
          </Badge>
        ) : (
          <Badge tone="outline">базовая модель</Badge>
        )}
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-[13px]">
        <dt className="text-[var(--muted-foreground)]">Датасет на ПК</dt>
        <dd className="tabular-nums">{pc.dataset ? `передом ${pc.dataset.front} · задом ${pc.dataset.rear}` : "—"}</dd>
        <dt className="text-[var(--muted-foreground)]">Последнее обучение</dt>
        <dd className="flex flex-wrap items-center gap-1.5">
          {training && status ? (
            <>
              <Badge tone={status.tone}>{status.label}</Badge>
              {training.ran_at && (
                <time dateTime={training.ran_at} className="tabular-nums">
                  {formatDateTime(training.ran_at)}
                </time>
              )}
            </>
          ) : (
            "ещё не запускалось"
          )}
        </dd>
        {training && (
          <>
            <dt className="text-[var(--muted-foreground)]">Точность</dt>
            <dd className="tabular-nums">
              {formatAccuracy(training.baseline?.accuracy)} → {formatAccuracy(training.candidate?.accuracy)}
            </dd>
            {training.samples != null && (
              <>
                <dt className="text-[var(--muted-foreground)]">Кадров в обучении</dt>
                <dd className="tabular-nums">{formatSamples(training.samples)}</dd>
              </>
            )}
            {training.reason && (
              <>
                <dt className="text-[var(--muted-foreground)]">Причина</dt>
                <dd className="text-[var(--muted-foreground)]">{training.reason}</dd>
              </>
            )}
          </>
        )}
      </dl>
    </section>
  );
}

function SummaryCard({
  summary,
  loading,
  error,
}: {
  summary: GrainOrientationSummary | null;
  loading: boolean;
  error: string;
}) {
  return (
    <Card className="mb-4">
      <CardContent className="grid gap-4 pt-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div>
          <h2 className="mb-2 text-sm font-semibold">Датасет в CRM</h2>
          {summary ? (
            <dl className="grid grid-cols-3 gap-2 sm:grid-cols-6">
              <Stat label="Всего" value={summary.total} />
              <Stat label="Передом" value={summary.by_label.front} />
              <Stat label="Задом" value={summary.by_label.rear} />
              <Stat label="Конфликты" value={summary.conflicts} warn />
              <Stat label="Исключено" value={summary.excluded} />
              <Stat label="Не отправлено" value={summary.unsent} />
            </dl>
          ) : (
            <p className="text-sm text-[var(--muted-foreground)]">
              {loading ? "Загрузка…" : error || "Сводка недоступна"}
            </p>
          )}
        </div>
        {summary && <CameraPcBlock pc={summary.camera_pc} />}
      </CardContent>
    </Card>
  );
}

function SampleCard({
  sample,
  canEdit,
  onChanged,
}: {
  sample: GrainOrientationSample;
  canEdit: boolean;
  onChanged: (row: GrainOrientationSample) => void;
}) {
  const [busy, setBusy] = useState<"" | GrainOrientationLabel | "exclude">("");
  const [error, setError] = useState("");
  // Подписанная ссылка меняется в каждом ответе; закрепляем её, чтобы опрос
  // раз в 30 с не заставлял браузер заново качать все фото сетки. Ссылка живёт
  // час, обновляем задолго до этого.
  const photoRef = useRef<{ url: string | null; at: number } | null>(null);
  const nextPhoto = apiFileUrl(sample.photo_url);
  if (
    !photoRef.current ||
    (nextPhoto === null) !== (photoRef.current.url === null) ||
    Date.now() - photoRef.current.at > PHOTO_LINK_REFRESH_MS
  ) {
    photoRef.current = { url: nextPhoto, at: Date.now() };
  }
  const photo = photoRef.current.url;
  const meta = LABEL_META[sample.label];
  const plate = sample.vehicle_number.trim();
  const alt = plate ? `Машина ${plate}` : "Машина на весах";

  async function post(
    action: "label" | "exclude",
    body: Record<string, unknown>,
    key: GrainOrientationLabel | "exclude",
  ) {
    setBusy(key);
    setError("");
    try {
      const res = await api.post<GrainOrientationSample>(`/grain/orientation-samples/${sample.id}/${action}/`, body);
      onChanged(res.data);
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy("");
    }
  }

  return (
    <article
      aria-label={`Кадр ${sample.sample_id}`}
      className={cn(
        "flex flex-col overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-card",
        sample.excluded && "opacity-60",
      )}
    >
      {photo ? (
        <a
          href={photo}
          target="_blank"
          rel="noreferrer"
          title="Открыть кадр в новой вкладке"
          className="block aspect-video overflow-hidden bg-black/5"
        >
          {/* eslint-disable-next-line @next/next/no-img-element -- подписанная ссылка бэкенда */}
          <img src={photo} alt={alt} loading="lazy" className="size-full object-cover" />
        </a>
      ) : (
        <div className="flex aspect-video items-center justify-center gap-1.5 bg-[var(--muted)]/60 text-xs text-[var(--muted-foreground)]">
          <Camera className="size-4" /> кадр недоступен
        </div>
      )}
      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={meta.tone} className="h-7 px-2.5 text-[13px]">
            {meta.badge}
          </Badge>
          <Badge tone="outline">{SOURCE_LABELS[sample.label_source]}</Badge>
          {sample.conflict && (
            <Badge tone="warning" dot>
              {modelBadge(sample.model_orientation)}
            </Badge>
          )}
          {sample.excluded && <Badge tone="muted">исключён</Badge>}
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-[var(--muted-foreground)]">
          <span className="font-semibold tabular-nums text-[var(--foreground)]">{formatKg(sample.weight_kg)}</span>
          <time dateTime={sample.captured_at} className="tabular-nums">
            {formatDateTime(sample.captured_at)}
          </time>
          {plate && <span className="font-mono font-semibold text-[var(--foreground)]">{plate}</span>}
          <span>{sample.sent_at ? "отправлен на ПК" : "ждёт отправки"}</span>
        </div>
        {sample.last_error && (
          <p className="truncate text-xs text-[var(--destructive)]" title={sample.last_error}>
            ошибка отправки: {sample.last_error}
          </p>
        )}
        {sample.reviewed_by_name && (
          <p className="text-xs text-[var(--muted-foreground)]">
            проверил {sample.reviewed_by_name}
            {sample.reviewed_at && ` · ${formatDateTime(sample.reviewed_at)}`}
          </p>
        )}
        {canEdit && (
          <div
            role="group"
            aria-label={`Метка кадра ${sample.sample_id}`}
            className="mt-auto flex flex-wrap items-center gap-1.5 pt-1"
          >
            {sample.excluded ? (
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== ""}
                onClick={() => void post("label", { label: sample.label }, sample.label)}
              >
                {busy ? <LoaderCircle className="animate-spin" /> : <Undo2 />} Вернуть
              </Button>
            ) : (
              <>
                {(["front", "rear"] as const).map((label) => {
                  const current = sample.label === label;
                  return (
                    <Button
                      key={label}
                      size="sm"
                      variant={current ? "default" : "outline"}
                      aria-pressed={current}
                      disabled={busy !== "" || current}
                      onClick={() => void post("label", { label }, label)}
                    >
                      {busy === label && <LoaderCircle className="animate-spin" />}
                      {LABEL_META[label].button}
                    </Button>
                  );
                })}
                <Button
                  size="sm"
                  variant="ghost"
                  className="ml-auto"
                  disabled={busy !== ""}
                  onClick={() => void post("exclude", {}, "exclude")}
                >
                  {busy === "exclude" ? <LoaderCircle className="animate-spin" /> : <Ban />} Исключить
                </Button>
              </>
            )}
          </div>
        )}
        {error && (
          <p role="alert" className="text-xs text-[var(--destructive)]">
            {error}
          </p>
        )}
      </div>
    </article>
  );
}

/**
 * Чистка датасета, когда модель уже дообучилась: кадры удаляются из CRM и с ПК
 * камер. Бэкенд удаляет пакетами, поэтому запрос повторяется, пока есть
 * `remaining`; ход, итог и ошибки показываем в самой модалке — со страницы их
 * не видно. Монтируется только открытой: состояние свежее при каждом открытии.
 */
function PurgeDialog({ open, onClose, onPurged }: { open: boolean; onClose: () => void; onPurged: () => void }) {
  const modeId = useId();
  const daysId = useId();
  const [mode, setMode] = useState<PurgeMode>("older");
  const [days, setDays] = useState(String(DEFAULT_PURGE_DAYS));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  /** Сумма deleted/removed_from_pc по всем пакетам; pc_unavailable и remaining — с последнего. */
  const [progress, setProgress] = useState<GrainOrientationPurgeResult | null>(null);

  const olderThanDays = mode === "older" ? purgeDays(days) : null;
  const invalid = mode === "older" && olderThanDays === null;
  // Итог показываем, когда пакеты кончились и не оборвались ошибкой; после
  // ошибки форма остаётся для повтора, а счётчик — под ней.
  const finished = progress !== null && !busy && !error;

  async function purge() {
    if (invalid || busy) return;
    setBusy(true);
    setError("");
    const body = { older_than_days: olderThanDays };
    // Повтор после ошибки продолжает счёт той же чистки.
    let totals = progress ?? { deleted: 0, removed_from_pc: 0, pc_unavailable: false, remaining: 0 };
    let batches = 0;
    try {
      for (let batch = 0; batch < MAX_PURGE_BATCHES; batch += 1) {
        const { data } = await api.post<GrainOrientationPurgeResult>(PURGE_URL, body);
        batches += 1;
        totals = {
          deleted: totals.deleted + data.deleted,
          removed_from_pc: totals.removed_from_pc + data.removed_from_pc,
          pc_unavailable: data.pc_unavailable,
          remaining: data.remaining,
        };
        setProgress(totals);
        // Без ПК следующий пакет оставит те же строки исключёнными — крутиться
        // до предела бессмысленно. Ответ без remaining считаем последним.
        if (data.pc_unavailable || !(data.remaining > 0)) break;
      }
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
      if (batches > 0) onPurged();
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        if (!busy) onClose();
      }}
      eyebrow="Подтверждение"
      title="Очистить датасет?"
      description="Кадры удаляются из CRM и с ПК камер без возможности восстановления."
      className="max-w-md"
      footer={
        finished ? (
          <Button type="button" variant="outline" onClick={onClose}>
            Готово
          </Button>
        ) : (
          <>
            <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
              Отмена
            </Button>
            <Button type="button" variant="destructive" onClick={() => void purge()} disabled={busy || invalid}>
              {busy ? "Удаление…" : "Удалить кадры"}
            </Button>
          </>
        )
      }
    >
      <div className="flex flex-col gap-3">
        {finished ? (
          <div role="status" className="flex flex-col gap-3">
            <dl className="grid grid-cols-2 gap-2">
              <Stat label="Удалено из CRM" value={progress.deleted} />
              <Stat label="Удалено с ПК" value={progress.removed_from_pc} />
            </dl>
            {progress.pc_unavailable ? (
              <p className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm font-medium text-amber-950">
                ПК камер недоступен: кадры, уже отправленные на ПК, остались в CRM как исключённые. Повторите очистку,
                когда ПК будет доступен
              </p>
            ) : (
              progress.remaining > 0 && (
                <p className="text-sm text-[var(--muted-foreground)]">
                  Осталось ещё {progress.remaining.toLocaleString("ru-RU")} кадров — повторите очистку.
                </p>
              )
            )}
          </div>
        ) : (
          <>
            <div>
              <Label htmlFor={modeId}>Какие кадры</Label>
              <Select
                id={modeId}
                value={mode}
                onChange={(event) => isPurgeMode(event.target.value) && setMode(event.target.value)}
                disabled={busy}
                data-autofocus
              >
                <option value="older">Старше N дней</option>
                <option value="all">Все кадры</option>
              </Select>
            </div>
            {mode === "older" && (
              <div>
                <Label htmlFor={daysId}>Старше, дней</Label>
                <Input
                  id={daysId}
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={MAX_PURGE_DAYS}
                  step={1}
                  value={days}
                  onChange={(event) => setDays(event.target.value)}
                  disabled={busy}
                />
                <p className="mt-1 text-[11px] text-[var(--muted-foreground)]">
                  Более свежие кадры останутся — на случай разбора конфликтов.
                </p>
              </div>
            )}
            {progress && (
              <p role="status" className="text-sm tabular-nums text-[var(--muted-foreground)]">
                Удалено {progress.deleted.toLocaleString("ru-RU")} кадров{busy ? "…" : ""}
              </p>
            )}
          </>
        )}
        {error && (
          <p
            role="alert"
            className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
          >
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}

function OrientationDatasetPageInner({ me }: { me: Me }) {
  const canEdit = me.is_superuser;
  const [scope, setScope] = useState<Scope>("all");
  const [purgeOpen, setPurgeOpen] = useState(false);
  const [labelFilter, setLabelFilter] = useState<LabelFilter>("all");
  /** Ответы своих правок поверх списка, пока опрос не принесёт их же с сервера. */
  const [overrides, setOverrides] = useState<Record<number, GrainOrientationSample>>({});

  const url = useMemo(() => {
    const query = new URLSearchParams(SCOPE_QUERY[scope]);
    if (labelFilter !== "all") query.set("label", labelFilter);
    const search = query.toString();
    return `${LIST_URL}${search ? `?${search}` : ""}`;
  }, [labelFilter, scope]);

  const list = usePagedApi<GrainOrientationSample>(url, PAGE_SIZE);
  const summary = useApi<GrainOrientationSummary>(SUMMARY_URL);

  function refresh() {
    return Promise.all([list.reload(), summary.reload()]);
  }

  // reload() возвращает первую страницу: пока оператор листает дальше, опрос
  // не должен схлопывать сетку — обновляем только счётчики.
  const singlePage = list.items.length <= PAGE_SIZE;
  const poll = useCallback(
    () => Promise.all([singlePage ? list.reload() : Promise.resolve(), summary.reload()]),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload-функции стабильны внутри хуков
    [singlePage],
  );
  useVisiblePolling(poll, POLL_INTERVAL_MS);

  function applyChange(row: GrainOrientationSample) {
    setOverrides((current) => ({ ...current, [row.id]: row }));
    void summary.reload();
  }

  // Удалённых кадров в ответах уже нет — сбрасываем и локальные правки поверх них.
  function handlePurged() {
    setOverrides({});
    void refresh();
  }

  const samples = list.items.map((row) => freshest(row, overrides[row.id]));
  const hasFilters = scope !== "all" || labelFilter !== "all";
  const initialLoading = list.loading && list.items.length === 0;

  const scopeTabs: TabDef[] = SCOPES.map((key) => ({
    key,
    label: SCOPE_LABELS[key],
    count: scopeCount(summary.data, key),
  }));
  // Счётчики по меткам считают весь датасет, поэтому показываем их только без фильтра.
  const labelTabs: TabDef[] = [
    { key: "all", label: "Все" },
    { key: "front", label: "Передом", count: scope === "all" ? summary.data?.by_label.front : undefined },
    { key: "rear", label: "Задом", count: scope === "all" ? summary.data?.by_label.rear : undefined },
  ];

  return (
    <AppShell
      title={PAGE_TITLE}
      section="Приход и вывоз"
      description="Кадры с весовой с меткой «передом = заезд, задом = выезд». Проверьте и поправьте метку, если модель или вес ошиблись"
      actions={
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="outline" size="sm" className="h-9" disabled={list.loading} onClick={() => void refresh()}>
            <RefreshCw className={cn(list.loading && "animate-spin")} /> Обновить
          </Button>
          {canEdit && (
            <Button
              variant="outline"
              size="sm"
              className="h-9 text-[var(--destructive)]"
              onClick={() => setPurgeOpen(true)}
            >
              <Trash2 /> Очистить датасет…
            </Button>
          )}
        </div>
      }
    >
      <SummaryCard summary={summary.data} loading={summary.loading} error={summary.error} />

      <Card>
        <CardContent className="pt-6">
          <div className="mb-4 flex flex-col gap-3">
            <div className="overflow-x-auto">
              <Tabs
                tabs={scopeTabs}
                active={scope}
                onChange={(key) => isScope(key) && setScope(key)}
                variant="segment"
                label="Фильтр кадров"
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Tabs
                tabs={labelTabs}
                active={labelFilter}
                onChange={(key) => isLabelFilter(key) && setLabelFilter(key)}
                variant="segment"
                label="Метка"
              />
              <div className="flex items-center gap-3 text-xs text-[var(--muted-foreground)]">
                <span aria-live="polite">{initialLoading ? "Загрузка…" : `Найдено: ${list.count}`}</span>
                <span className="inline-flex items-center gap-1.5">
                  <Radio className="size-3.5 text-[var(--success)]" /> Обновляется автоматически
                </span>
              </div>
            </div>
          </div>

          {initialLoading ? (
            <div className="py-12 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</div>
          ) : list.error && samples.length === 0 ? (
            <ErrorAlert message={list.error} onRetry={() => void list.reload()} />
          ) : samples.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-14 text-center">
              <span className="flex size-11 items-center justify-center rounded-full bg-[var(--muted)]">
                <Images className="size-5 text-[var(--muted-foreground)]" />
              </span>
              <p className="font-medium">{hasFilters ? "Кадров по этому фильтру нет" : "Кадров пока нет"}</p>
              <p className="text-sm text-[var(--muted-foreground)]">
                Датасет собирается автоматически из взвешиваний с фото.
              </p>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {samples.map((sample) => (
                  <SampleCard key={sample.id} sample={sample} canEdit={canEdit} onChanged={applyChange} />
                ))}
              </div>
              {list.error && (
                <div className="mt-3">
                  <ErrorAlert message={list.error} onRetry={() => void list.reload()} />
                </div>
              )}
              <LoadMore
                shown={samples.length}
                total={list.count}
                hasMore={list.hasMore}
                loading={list.loadingMore}
                onClick={list.loadMore}
              />
            </>
          )}
        </CardContent>
      </Card>

      {canEdit && purgeOpen && <PurgeDialog open onClose={() => setPurgeOpen(false)} onPurged={handlePurged} />}
    </AppShell>
  );
}

/**
 * Страница только для владельца: операторам она не показывается и не линкуется,
 * а бэкенд отвечает 403 всем, кроме суперпользователя. Пока сессия читается —
 * та же заглушка, что у RequirePerm.
 */
export default function OrientationDatasetPage() {
  const { me, loading } = useAuth();

  if (loading) {
    return (
      <AppShell title={PAGE_TITLE}>
        <p className="text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      </AppShell>
    );
  }
  if (!me?.is_superuser) {
    return (
      <AppShell title={PAGE_TITLE}>
        <NoAccessCard />
      </AppShell>
    );
  }
  return <OrientationDatasetPageInner me={me} />;
}
