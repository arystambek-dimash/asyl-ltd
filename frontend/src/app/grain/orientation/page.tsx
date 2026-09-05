"use client";

import { useCallback, useMemo, useRef, useState } from "react";
import { Ban, Camera, Cpu, Images, LoaderCircle, Radio, RefreshCw, Undo2 } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { LoadMore } from "@/components/ui/load-more";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { apiFileUrl, formatKg } from "@/lib/grain";
import type {
  GrainOrientationCameraPc,
  GrainOrientationLabel,
  GrainOrientationSample,
  GrainOrientationSummary,
  GrainOrientationTrainingReport,
  VehicleOrientation,
} from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn, formatDateTime } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const LIST_URL = "/grain/orientation-samples/";
const SUMMARY_URL = "/grain/orientation-samples/summary/";
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

function OrientationDatasetPageInner() {
  const { me } = useAuth();
  const canEdit = can(me, "grain.admin");
  const [scope, setScope] = useState<Scope>("all");
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
      title="Датасет ориентации"
      section="Приход и вывоз"
      description="Кадры с весовой с меткой «передом = заезд, задом = выезд». Проверьте и поправьте метку, если модель или вес ошиблись"
      actions={
        <Button
          variant="outline"
          size="sm"
          className="h-9 shrink-0"
          disabled={list.loading}
          onClick={() => void refresh()}
        >
          <RefreshCw className={cn(list.loading && "animate-spin")} /> Обновить
        </Button>
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
    </AppShell>
  );
}

export default function OrientationDatasetPage() {
  return (
    <RequirePerm perm="grain.view" title="Датасет ориентации">
      <OrientationDatasetPageInner />
    </RequirePerm>
  );
}
