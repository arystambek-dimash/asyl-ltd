"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CalendarClock,
  Check,
  ChevronDown,
  Clock3,
  LoaderCircle,
  PackageCheck,
  RefreshCw,
  Save,
  Warehouse,
} from "lucide-react";

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
import { GlowingEffect } from "@/components/ui/aceternity/glowing-effect";

const BATCH_META: Record<AlwaysOnStockBatch["status"], { label: string; className: string }> = {
  scheduled: { label: "Запланировано", className: "border-blue-200 bg-blue-50 text-blue-700" },
  blocked: { label: "Нужна настройка", className: "border-amber-200 bg-amber-50 text-amber-700" },
  posted: { label: "Приход создан", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  empty: { label: "Нет продукции", className: "border-slate-200 bg-slate-50 text-slate-500" },
  failed: { label: "Ошибка", className: "border-red-200 bg-red-50 text-red-700" },
};

/**
 * Порог «шума»: период другого цвета с числом мешков меньше этого значения
 * считается случайным вкраплением и приклеивается к предыдущему периоду.
 */
const NOISE_THRESHOLD = 7;

/**
 * «Поправленный» вид периодов: мелкие вкрапления другого цвета (< NOISE_THRESHOLD
 * мешков) поглощаются предыдущим периодом — их мешки приплюсовываются к нему,
 * а сам период исчезает. У поглощающего периода расширяем конец до конца
 * поглощённого, чтобы диапазон времени соответствовал выросшему числу мешков.
 *
 * Порядок повторяет исходный алгоритм: сравнение идёт с ПРЕДЫДУЩИМ оставленным
 * периодом, поэтому серия мелких вкраплений подряд склеивается в один якорь.
 * Сквозные периоды (is_partial_for_day) не участвуют в сравнении по числу
 * мешков — их model_bags не отражает реальность, поэтому они всегда остаются
 * якорями и сами не поглощаются.
 */
export function smoothColorRuns(runs: AlwaysOnProductionRun[]): AlwaysOnProductionRun[] {
  const result: AlwaysOnProductionRun[] = [];

  for (const run of runs) {
    const prev = result[result.length - 1];
    const mergeable =
      prev &&
      !prev.is_partial_for_day &&
      !run.is_partial_for_day &&
      normalizedColor(prev.color) !== normalizedColor(run.color) &&
      run.model_bags < NOISE_THRESHOLD;

    if (mergeable) {
      result[result.length - 1] = {
        ...prev,
        model_bags: prev.model_bags + run.model_bags,
        // Диапазон времени тянем до конца поглощённого вкрапления.
        last_counted_at: run.last_counted_at,
        ended_at: run.ended_at ?? prev.ended_at,
        ends_after_day: run.ends_after_day ?? prev.ends_after_day,
        // Если поглотили активный «хвост» — период продолжает идти.
        status: run.status === "active" ? run.status : prev.status,
      };
      continue;
    }

    result.push(run);
  }

  return result;
}

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

type DayRunView = "smoothed" | "raw";

/** Компактная лента для карточки выбранного дня в аналитике. */
export function AlwaysOnDayRunLog({ day, runs, timezone, loading, error, onRetry }: AlwaysOnDayRunLogProps) {
  const [view, setView] = useState<DayRunView>("smoothed");

  const orderedRuns = useMemo(
    () =>
      [...(runs ?? [])].sort(
        (left, right) => new Date(left.started_at).getTime() - new Date(right.started_at).getTime(),
      ),
    [runs],
  );
  const smoothedRuns = useMemo(() => smoothColorRuns(orderedRuns), [orderedRuns]);
  const visibleRuns = view === "smoothed" ? smoothedRuns : orderedRuns;
  const collapsedCount = orderedRuns.length - smoothedRuns.length;
  const showToggle = runs !== null && !loading && !error && collapsedCount > 0;

  return (
    <section className="mt-3 overflow-hidden rounded-xl border border-blue-200/80 bg-white">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-3 py-2.5">
        <Activity className="size-3.5 text-blue-600" />
        <h5 className="text-xs font-bold text-slate-700">Периоды цветов</h5>
        <span className="text-[10px] text-slate-400">за выбранный календарный день</span>
        {runs !== null && !loading && !error && (
          <span className="ml-auto rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-bold tabular-nums text-slate-500">
            {visibleRuns.length}
          </span>
        )}
        {showToggle && (
          <div className="flex basis-full items-center gap-1 pl-5.5">
            <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5">
              <button
                type="button"
                onClick={() => setView("smoothed")}
                aria-pressed={view === "smoothed"}
                className={cn(
                  "rounded-md px-2 py-1 text-[10px] font-bold transition",
                  view === "smoothed" ? "bg-white text-slate-800 shadow-sm" : "text-slate-400 hover:text-slate-600",
                )}
              >
                Поправленный
              </button>
              <button
                type="button"
                onClick={() => setView("raw")}
                aria-pressed={view === "raw"}
                className={cn(
                  "rounded-md px-2 py-1 text-[10px] font-bold transition",
                  view === "raw" ? "bg-white text-slate-800 shadow-sm" : "text-slate-400 hover:text-slate-600",
                )}
              >
                Сырой
              </button>
            </div>
            {view === "smoothed" && (
              <span className="text-[10px] text-slate-400">склеено вкраплений: {collapsedCount}</span>
            )}
          </div>
        )}
        <p className="basis-full pl-5.5 text-[10px] leading-relaxed text-slate-400">
          {view === "smoothed"
            ? `Мелкие вкрапления другого цвета (< ${NOISE_THRESHOLD} меш.) приклеены к предыдущему периоду. Переключите на «Сырой», чтобы увидеть исходный журнал.`
            : "Время первого и последнего мешка. Журнал сохраняется независимо от сдачи счётчика в архив."}
        </p>
      </div>

      {loading && runs === null ? (
        <div className="flex min-h-20 items-center justify-center gap-2 px-3 py-5 text-xs text-slate-400">
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
      ) : visibleRuns.length ? (
        <div className="max-h-[19rem] divide-y divide-slate-100 overflow-y-auto overscroll-contain">
          {visibleRuns.map((run) => {
            const meta = colorMeta(run.color);
            const active = run.status === "active";
            const partial = Boolean(run.is_partial_for_day);
            return (
              <div
                key={run.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 px-3 py-2.5 sm:grid-cols-[110px_minmax(0,1fr)_auto] sm:items-center"
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
        <p className="px-3 py-4 text-center text-xs text-slate-400">
          Детализация времени за {formatIsoDate(day)} недоступна. Журнал ведётся с момента обновления AI 24/7.
        </p>
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

      <section className="relative rounded-2xl bg-slate-950 p-4 text-white shadow-[0_16px_42px_rgba(15,23,42,0.12)] sm:p-5">
        <GlowingEffect
          disabled={false}
          glow
          spread={40}
          proximity={64}
          inactiveZone={0.5}
          borderWidth={2}
          className="motion-reduce:hidden"
        />
        <div className="relative grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(360px,0.85fr)] lg:items-center">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-white/45">
              <CalendarClock className="size-3.5" /> Автоприход на склад
            </div>
            <div className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="text-4xl font-black tabular-nums tracking-tight">{payload.close_time}</span>
              <span className="text-sm font-semibold text-white/45">каждый день</span>
            </div>
            <p className="mt-2 max-w-xl text-xs leading-relaxed text-white/50">
              В это время смена закрывается, а итог по цветам одним приходом добавляется на склад.
            </p>
          </div>

          <div className="grid gap-2.5 sm:grid-cols-2">
            <div className="rounded-xl bg-white/[0.07] px-3 py-2.5">
              <div className="text-[10px] font-bold uppercase tracking-wide text-white/35">Следующий запуск</div>
              <div className="mt-1 font-semibold tabular-nums text-white/85">{nextRun}</div>
              <div className="mt-0.5 text-[11px] text-white/35">{timezone}</div>
            </div>
            <div className="rounded-xl bg-white/[0.07] px-3 py-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-white/50">Готовность</span>
                <span className={cn("text-xs font-bold", missingColors.length ? "text-amber-300" : "text-emerald-300")}>
                  {missingColors.length ? "нужна настройка" : "готово"}
                </span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10">
                <div
                  className={cn("h-full rounded-full", missingColors.length ? "bg-amber-300" : "bg-emerald-400")}
                  style={{
                    width: `${draft.length ? ((draft.length - missingColors.length) / draft.length) * 100 : 100}%`,
                  }}
                />
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
        <button
          type="button"
          aria-expanded={mappingOpen}
          onClick={() => setMappingOpen((open) => !open)}
          className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition hover:bg-slate-50 sm:px-5"
        >
          <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-blue-600">
            <Warehouse className="size-4.5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block font-bold text-slate-900">Куда приходовать продукцию</span>
            <span className="mt-0.5 block truncate text-xs text-slate-500">
              Один раз сопоставьте распознанный цвет с товаром каталога.
            </span>
          </span>
          {missingColors.length > 0 ? (
            <span className="hidden shrink-0 items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-bold text-amber-700 sm:inline-flex">
              <AlertTriangle className="size-3.5" />
              {missingColors.length} нужно настроить
            </span>
          ) : (
            <span className="hidden shrink-0 items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-xs font-bold text-emerald-700 sm:inline-flex">
              <Check className="size-3.5" />
              Всё настроено
            </span>
          )}
          <ChevronDown
            className={cn(
              "size-5 shrink-0 text-slate-400 transition-transform duration-200",
              mappingOpen && "rotate-180",
            )}
          />
        </button>

        {mappingOpen && (
          <div className="border-t border-slate-100 px-4 pb-4 pt-4 sm:px-5 sm:pb-5">
            {missingColors.length > 0 && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-800">
                <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                <span>
                  Не настроено: <b>{missingColors.join(", ")}</b>. Подсчёт и журнал продолжат работать, но складской
                  приход будет ждать настройки — мешки не потеряются.
                </span>
              </div>
            )}

            <div className={cn("space-y-3", missingColors.length > 0 && "mt-4")}>
              {draft.map((mapping) => {
                const meta = colorMeta(mapping.color);
                const candidates = productOptions(payload.products, mapping.color, mapping.product);
                const hasMatchingProducts = payload.products.some(
                  (product) => normalizedColor(product.color) === normalizedColor(mapping.color),
                );
                return (
                  <label
                    key={mapping.color}
                    className="grid gap-2 rounded-xl border border-slate-200 bg-slate-50/70 p-3 sm:grid-cols-[150px_minmax(0,1fr)] sm:items-center"
                  >
                    <span className="flex items-center gap-2 text-sm font-bold text-slate-700">
                      <span className={cn("size-3 rounded-full", meta.dot)} /> {meta.label}
                    </span>
                    <span>
                      <Select
                        aria-label={`Товар для цвета ${meta.label}`}
                        value={mapping.product ?? ""}
                        disabled={!canManage}
                        onChange={(event) => updateMapping(mapping.color, event.target.value)}
                        className="bg-white"
                      >
                        <option value="">Не выбран — не приходовать</option>
                        {candidates.map((product) => (
                          <option key={product.id} value={product.id}>
                            {product.label}
                          </option>
                        ))}
                      </Select>
                      <span className="mt-1 block text-[10px] text-slate-400">
                        {hasMatchingProducts
                          ? `Показаны товары цвета «${meta.label}»${candidates.length !== payload.products.length ? "" : "."}`
                          : "В каталоге нет товара этого цвета — показан весь активный каталог."}
                      </span>
                    </span>
                  </label>
                );
              })}
              {!draft.length && (
                <div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-sm text-slate-400">
                  Цвета появятся здесь после первого распознавания модели.
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
        )}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
        <div className="flex items-center gap-2">
          <PackageCheck className="size-4 text-emerald-600" />
          <h3 className="font-bold text-slate-900">Предварительный приход</h3>
        </div>
        <p className="mt-1 text-xs text-slate-500">Что попадёт на склад при ближайшем закрытии смены.</p>

        <div className="mt-4 overflow-hidden rounded-xl border border-slate-200">
          {(payload.preview ?? []).map((row, index) => {
            const meta = colorMeta(row.color);
            return (
              <div key={row.color} className={cn("p-3", index > 0 && "border-t border-slate-200")}>
                <div className="flex items-center gap-2">
                  <span className={cn("size-2.5 rounded-full", meta.dot)} />
                  <span className="text-sm font-bold text-slate-700">{meta.label}</span>
                  <span className="ml-auto text-lg font-black tabular-nums text-slate-900">{row.net_bags}</span>
                  <span className="text-[10px] font-semibold uppercase text-slate-400">меш.</span>
                </div>
                <div className="mt-1.5 flex items-center gap-2 text-xs text-slate-500">
                  {row.configured ? (
                    <>
                      <Check className="size-3.5 text-emerald-500" />
                      <span className="truncate">{row.product_label}</span>
                    </>
                  ) : (
                    <>
                      <AlertTriangle className="size-3.5 text-amber-500" />
                      <span className="font-semibold text-amber-700">Товар не выбран</span>
                    </>
                  )}
                </div>
                {row.correction_bags !== 0 && (
                  <div className="mt-1 text-[10px] text-slate-400">
                    Модель {row.detected_bags} · поправка {row.correction_bags > 0 ? "+" : ""}
                    {row.correction_bags}
                  </div>
                )}
              </div>
            );
          })}
          {!payload.preview.length && (
            <div className="px-4 py-10 text-center text-sm text-slate-400">В текущей смене продукции пока нет.</div>
          )}
        </div>
        <p className="mt-3 flex items-start gap-2 text-[11px] leading-relaxed text-slate-400">
          <Clock3 className="mt-0.5 size-3.5 shrink-0" />
          До {payload.close_time} значения могут меняться. После создания прихода партия фиксируется и повторно не
          проводится.
        </p>
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-4 sm:p-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="font-bold text-slate-900">История складских приходов</h3>
            <p className="mt-1 text-xs text-slate-500">Отдельная партия на каждую камеру и производственный день.</p>
          </div>
          <span className="text-xs font-semibold text-slate-400">Последние {batches.length}</span>
        </div>

        {batches.length ? (
          <div className="mt-4 space-y-2.5">
            {batches.map((batch) => {
              const meta = BATCH_META[batch.status];
              const retryable = batch.status === "blocked" || batch.status === "failed";
              return (
                <article key={batch.id} className="rounded-xl border border-slate-200 p-3.5">
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="min-w-36">
                      <div className="text-sm font-bold text-slate-800">Смена {formatIsoDate(batch.business_day)}</div>
                      <div className="mt-0.5 text-[11px] text-slate-400">
                        запуск {zonedDateTime(batch.scheduled_for, timezone, false)} · попыток {batch.attempts}
                      </div>
                    </div>
                    <span
                      className={cn(
                        "rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide",
                        meta.className,
                      )}
                    >
                      {meta.label}
                    </span>
                    <div className="ml-auto text-right">
                      <div className="text-xl font-black tabular-nums text-slate-900">{batch.total_bags}</div>
                      <div className="text-[10px] uppercase text-slate-400">мешков</div>
                    </div>
                  </div>

                  {batch.items.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5 border-t border-slate-100 pt-3">
                      {batch.items.map((item) => (
                        <span key={item.id} className="rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-600">
                          <b>{colorMeta(item.color).label}</b> · {item.product_label} · {item.posted_bags} меш.
                        </span>
                      ))}
                    </div>
                  )}

                  {(batch.last_error || retryable) && (
                    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
                      {batch.last_error && <p className="min-w-0 flex-1 text-xs text-red-600">{batch.last_error}</p>}
                      {canManage && retryable && onRetry && (
                        <Button variant="outline" size="sm" onClick={() => void onRetry(batch)}>
                          <RefreshCw /> Повторить сейчас
                        </Button>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <div className="mt-4 rounded-xl border border-dashed border-slate-200 px-4 py-10 text-center text-sm text-slate-400">
            Первый складской приход появится после ближайшего запуска в {payload.close_time}.
          </div>
        )}
      </section>
    </div>
  );
}
