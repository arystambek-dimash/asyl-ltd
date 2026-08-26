"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, Clock3, LoaderCircle, RefreshCw, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import type {
  AlwaysOnProductMapping,
  AlwaysOnProductionPayload,
  AlwaysOnProductionProduct,
  AlwaysOnProductionRun,
  AlwaysOnStockBatch,
} from "@/lib/types";
import { cn, formatIsoDate } from "@/lib/utils";
import { colorMeta, normalizedColor } from "@/lib/monoblock-colors";
import { ColorDot, Eyebrow, Hairline, InfoHint, Panel, SectionHead, StatusChip } from "@/components/monoblock/ui";

const BATCH_META: Record<AlwaysOnStockBatch["status"], { label: string; className: string }> = {
  scheduled: { label: "Запланировано", className: "bg-blue-50 text-blue-600" },
  blocked: { label: "Нужна настройка", className: "bg-amber-50 text-amber-600" },
  posted: { label: "Создан", className: "bg-emerald-50 text-emerald-600" },
  empty: { label: "Нет продукции", className: "bg-slate-100 text-slate-500" },
  failed: { label: "Ошибка", className: "bg-red-50 text-red-600" },
};

function zonedDateTime(value: string, timezone: string, withDate = true) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone: timezone,
    ...(withDate ? { day: "2-digit", month: "2-digit", year: "numeric" } : {}),
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function productOptions(products: AlwaysOnProductionProduct[], color: string, selectedProduct: number | null) {
  const matching = products.filter((product) => normalizedColor(product.color) === normalizedColor(color));
  if (!matching.length) return products;

  const selected = products.find((product) => product.id === selectedProduct);
  if (!selected || matching.some((product) => product.id === selected.id)) return matching;
  // Старую несовпадающую настройку не прячем: оператор должен сначала увидеть,
  // куда сейчас идёт продукция, и только потом осознанно заменить товар.
  return [selected, ...matching];
}

function mappingSignature(rows: AlwaysOnProductMapping[]) {
  return [...rows]
    .sort((left, right) => left.color.localeCompare(right.color))
    .map((row) => `${normalizedColor(row.color)}:${row.product ?? ""}`)
    .join("|");
}

interface AlwaysOnProductionPanelProps {
  payload: AlwaysOnProductionPayload | null;
  loading: boolean;
  error: string | null;
  saving: boolean;
  canManage: boolean;
  onSave: (mappings: AlwaysOnProductMapping[]) => void | Promise<void>;
  onRetry?: (batch: AlwaysOnStockBatch) => void | Promise<void>;
}

interface AlwaysOnDayRunLogProps {
  day: string;
  runs: AlwaysOnProductionRun[] | null;
  timezone: string;
  loading: boolean;
  error?: string | null;
  onRetry?: () => void;
}

/** Компактная лента для карточки выбранного дня в аналитике. */
export function AlwaysOnDayRunLog({ day, runs, timezone, loading, error, onRetry }: AlwaysOnDayRunLogProps) {
  const orderedRuns = useMemo(
    () =>
      [...(runs ?? [])].sort(
        (left, right) =>
          new Date(left.started_at).getTime() - new Date(right.started_at).getTime() || left.id - right.id,
      ),
    [runs],
  );

  return (
    <section className="mt-4">
      <div className="flex flex-wrap items-center gap-2">
        <h5 className="text-[13px] font-semibold tracking-tight text-slate-800">Периоды цветов</h5>
        {runs !== null && !loading && !error && (
          <span className="text-[11px] font-medium tabular-nums text-slate-400">{orderedRuns.length}</span>
        )}
      </div>

      {loading && runs === null ? (
        <div className="mt-3 flex min-h-20 items-center justify-center gap-2 py-5 text-xs text-slate-400">
          <LoaderCircle className="size-4 animate-spin" /> Загружаем периоды дня…
        </div>
      ) : error ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 px-3 py-3 text-xs text-red-600">
          <AlertTriangle className="size-3.5 shrink-0" />
          <span className="min-w-0 flex-1">{error}</span>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="inline-flex items-center gap-1 rounded-md border border-red-200 bg-red-50 px-2 py-1 font-semibold transition hover:bg-red-100"
            >
              <RefreshCw className="size-3" /> Повторить
            </button>
          )}
        </div>
      ) : orderedRuns.length ? (
        <div className="mt-2 max-h-[19rem] divide-y divide-slate-100 overflow-y-auto overscroll-contain">
          {orderedRuns.map((run) => {
            const meta = colorMeta(run.color);
            const active = run.status === "active";
            const partial = Boolean(run.is_partial_for_day);
            return (
              <div
                key={run.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 py-2.5 sm:grid-cols-[110px_minmax(0,1fr)_auto] sm:items-center"
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span className={cn("size-2.5 shrink-0 rounded-full", meta.dot, active && "animate-pulse")} />
                  <span className="truncate text-xs font-bold text-slate-700">{meta.label}</span>
                </div>
                <div className="col-span-2 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-[11px] text-slate-500 sm:col-span-1">
                  <Clock3 className="size-3 shrink-0 text-slate-400" />
                  <span className="font-semibold tabular-nums text-slate-700">
                    {run.starts_before_day ? "с 00:00" : zonedDateTime(run.started_at, timezone, false)}
                  </span>
                  <span className="text-slate-300">—</span>
                  {run.ends_after_day ? (
                    <span className="font-semibold text-slate-700">до конца дня</span>
                  ) : active ? (
                    <span className="rounded-full bg-emerald-50 px-1.5 py-0.5 font-bold text-emerald-700">
                      идёт сейчас
                    </span>
                  ) : (
                    <span className="font-semibold tabular-nums text-slate-700">
                      {zonedDateTime(run.ended_at ?? run.last_counted_at, timezone, false)}
                    </span>
                  )}
                  {run.is_approximate && (
                    <span
                      title="Время восстановлено по первому доступному замеру"
                      className="rounded bg-amber-50 px-1.5 py-0.5 font-semibold text-amber-700"
                    >
                      ≈ приблизительно
                    </span>
                  )}
                </div>
                <div className="row-start-1 text-right sm:col-start-3">
                  {partial ? (
                    <span
                      title="Точное число мешков этой части берётся из итогов выбранного дня"
                      className="inline-flex rounded-full bg-amber-50 px-2 py-1 text-[9px] font-bold uppercase tracking-wide text-amber-700"
                    >
                      сквозной период
                    </span>
                  ) : (
                    <>
                      <span className="text-sm font-black tabular-nums text-slate-900">{run.model_bags}</span>
                      <span className="ml-1 text-[10px] text-slate-400">меш.</span>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="mt-2 py-4 text-center text-xs text-slate-400">Детализация за {formatIsoDate(day)} недоступна.</p>
      )}
    </section>
  );
}

export function AlwaysOnProductionPanel({
  payload,
  loading,
  error,
  saving,
  canManage,
  onSave,
  onRetry,
}: AlwaysOnProductionPanelProps) {
  const [draft, setDraft] = useState<AlwaysOnProductMapping[]>([]);
  // Сопоставление цвет→товар скрыто под сворачиваемой секцией, чтобы не
  // загромождать вкладку. Раскрываем автоматически, только пока остаются
  // ненастроенные цвета — иначе оператор может не заметить, что приход ждёт
  // настройки. После настройки всех цветов остаётся под кнопкой.
  const [mappingOpen, setMappingOpen] = useState(false);

  useEffect(() => {
    if (!payload) return;
    const byColor = new Map(payload.mappings.map((row) => [normalizedColor(row.color), row]));
    const nextDraft = payload.available_colors.map((color) => {
      const current = byColor.get(normalizedColor(color));
      return current ?? { color, product: null, product_label: null };
    });
    setDraft(nextDraft);
    setMappingOpen(nextDraft.some((row) => row.product === null));
  }, [payload]);

  const dirty = useMemo(
    () => Boolean(payload && mappingSignature(draft) !== mappingSignature(payload.mappings)),
    [draft, payload],
  );
  const missingColors = draft.filter((row) => row.product === null).map((row) => colorMeta(row.color).label);
  const batches = useMemo(
    () =>
      [...(payload?.batches ?? [])].sort(
        (left, right) => new Date(right.scheduled_for).getTime() - new Date(left.scheduled_for).getTime(),
      ),
    [payload?.batches],
  );

  if (loading && !payload) {
    return (
      <div className="flex min-h-72 items-center justify-center rounded-2xl border border-slate-200 bg-white text-slate-400">
        <LoaderCircle className="mr-2 size-5 animate-spin" /> Загружаем настройки автоприхода…
      </div>
    );
  }

  if (!payload) {
    return (
      <div
        role="alert"
        className="flex min-h-48 flex-col items-center justify-center rounded-2xl border border-red-200 bg-red-50 px-6 text-center text-red-700"
      >
        <AlertTriangle className="mb-2 size-6" />
        <p className="font-semibold">Настройки автоприхода недоступны</p>
        <p className="mt-1 max-w-lg text-sm text-red-600">{error || "Сервер не вернул данные по этой камере."}</p>
      </div>
    );
  }

  const timezone = payload.timezone || "Asia/Almaty";
  const nextRun = zonedDateTime(payload.next_run_at, timezone);

  function updateMapping(color: string, value: string) {
    if (!canManage) return;
    const productId = value ? Number(value) : null;
    const product = payload?.products.find((row) => row.id === productId) ?? null;
    setDraft((current) =>
      current.map((row) =>
        normalizedColor(row.color) === normalizedColor(color)
          ? { ...row, product: productId, product_label: product?.label ?? null }
          : row,
      ),
    );
  }

  return (
    <div className="space-y-4">
      {error && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700"
        >
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <Panel className="p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-6">
          <div>
            <Eyebrow>Автоприход</Eyebrow>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-5xl font-black tabular-nums tracking-tight text-slate-900">
                {payload.close_time}
              </span>
              <span className="text-sm font-medium text-slate-400">каждый день</span>
            </div>
          </div>
          <div className="flex flex-wrap items-start gap-x-10 gap-y-4">
            <div>
              <Eyebrow>Следующий запуск</Eyebrow>
              <div className="mt-1.5 font-semibold tabular-nums text-slate-800">{nextRun}</div>
              <div className="mt-0.5 text-[11px] text-slate-400">{timezone}</div>
            </div>
            <div>
              <Eyebrow>Готовность</Eyebrow>
              <div className="mt-2">
                {missingColors.length ? (
                  <StatusChip tone="warn">нужна настройка</StatusChip>
                ) : (
                  <StatusChip tone="ok">готово</StatusChip>
                )}
              </div>
            </div>
          </div>
        </div>
      </Panel>

      <Panel>
        <button
          type="button"
          aria-expanded={mappingOpen}
          onClick={() => setMappingOpen((open) => !open)}
          className="flex w-full items-center gap-3 px-5 py-4 text-left transition hover:bg-slate-50/60"
        >
          <span className="min-w-0 flex-1">
            <SectionHead
              title="Куда приходовать"
              hint="Один раз сопоставьте распознанный цвет с товаром каталога. Подсчёт и журнал работают независимо — мешки не потеряются."
            />
          </span>
          {missingColors.length > 0 ? (
            <StatusChip tone="warn">
              {missingColors.length} из {draft.length}
            </StatusChip>
          ) : (
            <StatusChip tone="ok">всё готово</StatusChip>
          )}
          <ChevronDown
            className={cn(
              "size-5 shrink-0 text-slate-300 transition-transform duration-200",
              mappingOpen && "rotate-180",
            )}
          />
        </button>

        {mappingOpen && (
          <>
            <Hairline />
            <div className="px-5 pb-5 pt-4">
              <div className="space-y-2">
                {draft.map((mapping) => {
                  const meta = colorMeta(mapping.color);
                  const candidates = productOptions(payload.products, mapping.color, mapping.product);
                  return (
                    <label
                      key={mapping.color}
                      className="grid gap-2 sm:grid-cols-[130px_minmax(0,1fr)] sm:items-center"
                    >
                      <span className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                        <ColorDot className={meta.dot} /> {meta.label}
                      </span>
                      <Select
                        aria-label={`Товар для цвета ${meta.label}`}
                        value={mapping.product ?? ""}
                        disabled={!canManage}
                        onChange={(event) => updateMapping(mapping.color, event.target.value)}
                        className="bg-white"
                      >
                        <option value="">Не выбран</option>
                        {candidates.map((product) => (
                          <option key={product.id} value={product.id}>
                            {product.label}
                          </option>
                        ))}
                      </Select>
                    </label>
                  );
                })}
                {!draft.length && (
                  <div className="px-4 py-8 text-center text-sm text-slate-400">
                    Цвета появятся после первого распознавания модели.
                  </div>
                )}
              </div>

              {canManage && (
                <div className="mt-4 flex justify-end">
                  <Button size="sm" disabled={!dirty || saving} onClick={() => void onSave(draft)}>
                    {saving ? <LoaderCircle className="animate-spin" /> : <Save />}
                    {saving ? "Сохраняем…" : "Сохранить"}
                  </Button>
                </div>
              )}
            </div>
          </>
        )}
      </Panel>

      <Panel className="p-5 sm:p-6">
        <SectionHead
          title="Предварительный приход"
          hint={`До ${payload.close_time} значения могут меняться. После создания прихода партия фиксируется и повторно не проводится.`}
          aside={<span className="text-[11px] font-medium text-slate-400">до {payload.close_time}</span>}
        />

        {payload.preview.length ? (
          <div className="mt-5 grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-3">
            {payload.preview.map((row) => {
              const meta = colorMeta(row.color);
              return (
                <div key={row.color}>
                  <div className="flex items-center gap-2">
                    <ColorDot className={meta.dot} />
                    <span className="text-xs font-medium text-slate-500">{meta.label}</span>
                    {!row.configured && (
                      <InfoHint text="Товар не выбран — этот цвет не попадёт на склад до настройки." />
                    )}
                  </div>
                  <div className="mt-1.5 flex items-baseline gap-1">
                    <span className="text-3xl font-black tabular-nums tracking-tight text-slate-900">
                      {row.net_bags}
                    </span>
                    <span className="text-xs font-medium text-slate-400">меш.</span>
                  </div>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="mt-5 py-8 text-center text-sm text-slate-400">В текущей смене продукции пока нет.</div>
        )}
      </Panel>

      <Panel className="p-5 sm:p-6">
        <SectionHead
          title="История приходов"
          aside={<span className="text-[11px] font-medium text-slate-400">последние {batches.length}</span>}
        />

        {batches.length ? (
          <div className="mt-4 space-y-1">
            {batches.map((batch, index) => {
              const meta = BATCH_META[batch.status];
              const retryable = batch.status === "blocked" || batch.status === "failed";
              return (
                <article key={batch.id}>
                  {index > 0 && <Hairline className="my-1" />}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                    <div className="min-w-32">
                      <div className="text-sm font-semibold text-slate-800">{formatIsoDate(batch.business_day)}</div>
                      <div className="mt-0.5 text-[11px] text-slate-400">
                        {zonedDateTime(batch.scheduled_for, timezone, false)}
                      </div>
                    </div>
                    <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-semibold", meta.className)}>
                      {meta.label}
                    </span>
                    <div className="ml-auto flex items-baseline gap-1">
                      <span className="text-lg font-black tabular-nums text-slate-900">{batch.total_bags}</span>
                      <span className="text-[10px] text-slate-400">меш.</span>
                    </div>
                  </div>

                  {(batch.last_error || retryable) && (
                    <div className="flex flex-wrap items-center gap-2 pb-2">
                      {batch.last_error && <p className="min-w-0 flex-1 text-xs text-red-600">{batch.last_error}</p>}
                      {canManage && retryable && onRetry && (
                        <Button variant="outline" size="sm" onClick={() => void onRetry(batch)}>
                          <RefreshCw /> Повторить
                        </Button>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <div className="mt-4 py-8 text-center text-sm text-slate-400">
            Первый приход появится после запуска в {payload.close_time}.
          </div>
        )}
      </Panel>
    </div>
  );
}
