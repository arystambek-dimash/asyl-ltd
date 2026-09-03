"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, Clock3, LoaderCircle, RefreshCw, Save, Warehouse } from "lucide-react";

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

// Keep this in sync with backend production.BASE_COLORS. White and any new
// detector label can be mapped to any product from the selected warehouse.
const PRODUCT_COLOR_RESTRICTED = new Set(["red", "green", "blue"]);
// Phase A remains a safe rollback target: until it is finalized, an existing
// catalogue product can only be selected where its stock card already exists.
// Phase B flips this and lets camera mapping create a card in another warehouse.
const MULTI_WAREHOUSE_PRODUCT_ASSIGNMENT_ENABLED = true;

function productHasStockCard(product: AlwaysOnProductionProduct, warehouse: number | null) {
  if (warehouse === null) return false;
  if (product.warehouse_ids !== undefined) {
    return product.warehouse_ids.includes(warehouse);
  }
  return product.warehouse === undefined || product.warehouse === warehouse;
}

function productCanBeAssigned(product: AlwaysOnProductionProduct, warehouse: number | null) {
  if (warehouse === null) return true;
  if (MULTI_WAREHOUSE_PRODUCT_ASSIGNMENT_ENABLED) return true;
  if (product.warehouse_ids !== undefined) {
    return product.warehouse_ids.length === 0 || productHasStockCard(product, warehouse);
  }
  return product.warehouse == null || product.warehouse === warehouse;
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

function productOptions(
  products: AlwaysOnProductionProduct[],
  color: string,
  selectedProduct: number | null,
  warehouse: number | null,
) {
  const available = products.filter((product) => productCanBeAssigned(product, warehouse));
  const matching = available.filter((product) => normalizedColor(product.color) === normalizedColor(color));
  const selected = products.find((product) => product.id === selectedProduct);
  // The model can report a new/unclassified color. The backend deliberately
  // allows such a row to map to any product from this warehouse; known colors
  // remain restricted so an operator cannot accidentally bind red to blue.
  const candidates = PRODUCT_COLOR_RESTRICTED.has(normalizedColor(color)) ? matching : available;
  if (!selected || candidates.some((product) => product.id === selected.id)) return candidates;
  // Старую несовпадающую настройку не прячем: оператор должен сначала увидеть,
  // куда сейчас идёт продукция, и только потом осознанно заменить товар.
  return [selected, ...candidates];
}

function mappingSignature(rows: AlwaysOnProductMapping[]) {
  return [...rows]
    .sort((left, right) => left.color.localeCompare(right.color))
    .map((row) => `${normalizedColor(row.color)}:${row.product ?? ""}`)
    .join("|");
}

function mappingNeedsConfiguration(
  mapping: AlwaysOnProductMapping,
  products: AlwaysOnProductionProduct[],
  warehouse: number | null,
) {
  if (mapping.product === null) return true;
  const product = products.find((row) => row.id === mapping.product);
  if (!product) return true;
  if (warehouse === null) return true;
  return !productHasStockCard(product, warehouse);
}

export type AlwaysOnReceiptMappingStatus = "ready" | "loading" | "unavailable";

export interface AlwaysOnReceiptMappingContext {
  status: AlwaysOnReceiptMappingStatus;
  mappings?: AlwaysOnProductMapping[] | null;
  products?: AlwaysOnProductionProduct[] | null;
  warehouse?: number | null;
  warehouseName?: string | null;
}

export type AlwaysOnReceiptDestination =
  | { state: "bound"; productLabel: string; warehouseName: string | null }
  | { state: "unbound" | "loading" | "unavailable" };

/** Resolves the current camera route shown in analytics; it is not a historical snapshot. */
export function resolveAlwaysOnReceiptDestination(
  context: AlwaysOnReceiptMappingContext,
  color: string,
): AlwaysOnReceiptDestination {
  if (context.status !== "ready") return { state: context.status };

  const mapping = (context.mappings ?? []).find((row) => normalizedColor(row.color) === normalizedColor(color));
  if (mapping?.product == null || !mapping.product_label || context.warehouse === null) {
    return { state: "unbound" };
  }

  // When the product catalogue is present, stale/inactive products and products
  // from another warehouse are no longer valid bindings. An omitted catalogue is
  // accepted only for rolling compatibility with the previous API response.
  if (context.products) {
    const product = context.products.find((row) => row.id === mapping.product);
    if (!product || (context.warehouse !== undefined && !productHasStockCard(product, context.warehouse))) {
      return { state: "unbound" };
    }
  }

  return {
    state: "bound",
    productLabel: mapping.product_label,
    warehouseName: context.warehouseName ?? (context.warehouse === undefined ? null : `Склад #${context.warehouse}`),
  };
}

/** Compact `product → warehouse` label shared by summaries, day cards and runs. */
export function AlwaysOnReceiptDestinationLabel({
  destination,
  colorLabel,
  showProduct = true,
  className,
}: {
  destination: AlwaysOnReceiptDestination;
  colorLabel?: string;
  showProduct?: boolean;
  className?: string;
}) {
  const accessiblePrefix = colorLabel ? `${colorLabel}: приход — ` : "Приход — ";
  if (destination.state === "unbound") {
    return (
      <span
        data-receipt-binding="unbound"
        className={cn(
          "inline-flex w-fit items-center gap-1 rounded-md border border-red-200 bg-red-50 px-1.5 py-0.5 text-[11px] font-bold text-red-700",
          className,
        )}
      >
        <span className="sr-only">{accessiblePrefix}</span>
        <AlertTriangle aria-hidden="true" className="size-3 shrink-0" /> Не привязан
      </span>
    );
  }

  if (destination.state !== "bound") {
    return (
      <span className={cn("text-xs font-medium text-slate-500", className)}>
        <span className="sr-only">{accessiblePrefix}</span>
        {destination.state === "loading" ? "Загрузка сопоставления…" : "Сопоставление недоступно"}
      </span>
    );
  }

  const title = destination.warehouseName
    ? `${accessiblePrefix}${destination.productLabel}, склад ${destination.warehouseName}`
    : `${accessiblePrefix}${destination.productLabel}`;
  if (!showProduct && !destination.warehouseName) return null;

  return (
    <span
      data-receipt-binding="bound"
      title={title}
      className={cn("flex min-w-0 flex-wrap items-center gap-x-1 text-xs leading-tight", className)}
    >
      <span className="sr-only">{accessiblePrefix}</span>
      {showProduct && <span className="min-w-0 font-semibold text-slate-700">{destination.productLabel}</span>}
      {destination.warehouseName && (
        <span className="inline-flex min-w-0 items-center gap-1 font-medium text-slate-500">
          <span aria-hidden="true">→</span>
          <span className="sr-only">склад </span>
          <Warehouse aria-hidden="true" className="size-3 shrink-0" />
          <span>{destination.warehouseName}</span>
        </span>
      )}
    </span>
  );
}

interface AlwaysOnProductionPanelProps {
  payload: AlwaysOnProductionPayload | null;
  loading: boolean;
  error: string | null;
  saving: boolean;
  canManage: boolean;
  onSave: (mappings: AlwaysOnProductMapping[], warehouse: number | null) => void | Promise<void>;
  onRetry?: (batch: AlwaysOnStockBatch) => void | Promise<void>;
}

interface AlwaysOnDayRunLogProps {
  day: string;
  runs: AlwaysOnProductionRun[] | null;
  timezone: string;
  loading: boolean;
  error?: string | null;
  unavailableReason?: string | null;
  receiptMapping?: AlwaysOnReceiptMappingContext;
  onRetry?: () => void;
}

export type AlwaysOnDayColorView = "algorithm" | "raw";

export function AlwaysOnDayColorViewToggle({
  view,
  nMin,
  onChange,
}: {
  view: AlwaysOnDayColorView;
  nMin: number;
  onChange: (view: AlwaysOnDayColorView) => void;
}) {
  return (
    <>
      <span className="text-[11px] font-semibold text-slate-400">Цвета:</span>
      <InfoHint
        text={`Алгоритм объединяет соседние одинаковые периоды и меняет короткий период (< ${nMin} меш.) только между двумя периодами одного другого цвета. Сырые данные не меняются.`}
      />
      <div
        role="group"
        aria-label="Отображение цветовой аналитики"
        className="inline-flex rounded-lg bg-slate-100 p-0.5"
      >
        <button
          type="button"
          aria-pressed={view === "algorithm"}
          onClick={() => onChange("algorithm")}
          className={cn(
            "rounded-md px-2.5 py-1 text-[11px] font-semibold transition",
            view === "algorithm" ? "bg-white text-slate-800 shadow-sm" : "text-slate-400 hover:text-slate-600",
          )}
        >
          Алгоритм
        </button>
        <button
          type="button"
          aria-pressed={view === "raw"}
          onClick={() => onChange("raw")}
          className={cn(
            "rounded-md px-2.5 py-1 text-[11px] font-semibold transition",
            view === "raw" ? "bg-white text-slate-800 shadow-sm" : "text-slate-400 hover:text-slate-600",
          )}
        >
          Сырые данные
        </button>
      </div>
    </>
  );
}

/** Компактная лента для карточки выбранного дня в аналитике. */
export function AlwaysOnDayRunLog({
  day,
  runs,
  timezone,
  loading,
  error,
  unavailableReason,
  receiptMapping,
  onRetry,
}: AlwaysOnDayRunLogProps) {
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
        {runs !== null && !loading && !error && !unavailableReason && (
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
      ) : unavailableReason ? (
        <div
          role="status"
          className="mt-3 flex items-start gap-2 rounded-xl bg-amber-50 px-3 py-3 text-xs text-amber-800"
        >
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{unavailableReason}</span>
        </div>
      ) : orderedRuns.length ? (
        <div className="mt-2 max-h-[19rem] divide-y divide-slate-100 overflow-y-auto overscroll-contain">
          {orderedRuns.map((run) => {
            const meta = colorMeta(run.color);
            const destination = receiptMapping
              ? resolveAlwaysOnReceiptDestination(receiptMapping, run.color)
              : undefined;
            const hasProduct = destination?.state === "bound";
            const active = run.status === "active";
            const partial = Boolean(run.is_partial_for_day);
            return (
              <div
                key={run.id}
                role="group"
                aria-label={
                  partial
                    ? `Период ${hasProduct ? destination.productLabel : meta.label}: сквозной период`
                    : `Период ${hasProduct ? destination.productLabel : meta.label}: ${run.model_bags} мешков`
                }
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 py-2.5 sm:grid-cols-[minmax(220px,1.25fr)_minmax(150px,1fr)_auto] sm:items-center"
              >
                <div className="min-w-0">
                  {hasProduct ? (
                    <div className="flex min-w-0 items-start gap-2">
                      <span
                        className={cn("mt-0.5 size-2.5 shrink-0 rounded-full", meta.dot, active && "animate-pulse")}
                      />
                      {destination && (
                        <AlwaysOnReceiptDestinationLabel destination={destination} colorLabel={undefined} />
                      )}
                    </div>
                  ) : (
                    <div className="flex min-w-0 items-center gap-2">
                      <span className={cn("size-2.5 shrink-0 rounded-full", meta.dot, active && "animate-pulse")} />
                      <span className="truncate text-xs font-bold text-slate-700">{meta.label}</span>
                    </div>
                  )}
                  {destination && !hasProduct && (
                    <AlwaysOnReceiptDestinationLabel
                      destination={destination}
                      colorLabel={meta.label}
                      className="mt-1 pl-[18px]"
                    />
                  )}
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
  const [warehouseDraft, setWarehouseDraft] = useState<number | null>(null);
  // Сопоставление цвет→товар скрыто под сворачиваемой секцией, чтобы не
  // загромождать вкладку. Раскрываем автоматически, только пока остаются
  // ненастроенные цвета — иначе оператор может не заметить, что приход ждёт
  // настройки. После настройки всех цветов остаётся под кнопкой.
  const [mappingOpen, setMappingOpen] = useState(false);
  const syncedCamera = useRef<string | null>(null);
  const baselineMappingSignature = useRef("");
  const baselineWarehouse = useRef<number | null>(null);
  const savingRef = useRef(saving);
  const draftRef = useRef(draft);
  const warehouseDraftRef = useRef(warehouseDraft);
  draftRef.current = draft;
  warehouseDraftRef.current = warehouseDraft;

  useEffect(() => {
    if (!payload) return;
    const incomingSignature = mappingSignature(payload.mappings);
    const incomingWarehouse = payload.warehouse ?? null;
    const draftSignature = mappingSignature(draftRef.current);
    const sameCamera = syncedCamera.current === payload.camera;
    const hasLocalChanges =
      sameCamera &&
      (draftSignature !== baselineMappingSignature.current || warehouseDraftRef.current !== baselineWarehouse.current);
    const incomingMatchesDraft =
      draftSignature === incomingSignature && warehouseDraftRef.current === incomingWarehouse;
    const saveJustFinished = savingRef.current && !saving;
    const serverChangedFromBaseline =
      incomingSignature !== baselineMappingSignature.current || incomingWarehouse !== baselineWarehouse.current;
    savingRef.current = saving;

    if (
      sameCamera &&
      (saving || hasLocalChanges) &&
      !incomingMatchesDraft &&
      !(saveJustFinished && serverChangedFromBaseline)
    ) {
      return;
    }
    const byColor = new Map(payload.mappings.map((row) => [normalizedColor(row.color), row]));
    const nextDraft = payload.available_colors.map((color) => {
      const current = byColor.get(normalizedColor(color));
      return current ?? { color, product: null, product_label: null };
    });
    setDraft(nextDraft);
    const nextWarehouse = incomingWarehouse;
    setWarehouseDraft(nextWarehouse);
    setMappingOpen(
      payload.fully_configured === false ||
        nextDraft.some((row) => mappingNeedsConfiguration(row, payload.products, nextWarehouse)),
    );
    syncedCamera.current = payload.camera;
    baselineMappingSignature.current = incomingSignature;
    baselineWarehouse.current = incomingWarehouse;
  }, [payload, saving]);

  const dirty = useMemo(
    () =>
      Boolean(
        payload &&
        (mappingSignature(draft) !== mappingSignature(payload.mappings) ||
          warehouseDraft !== (payload.warehouse ?? null)),
      ),
    [draft, payload, warehouseDraft],
  );
  const missingColors = draft
    .filter((row) => mappingNeedsConfiguration(row, payload?.products ?? [], warehouseDraft))
    .map((row) => colorMeta(row.color).label);
  const canRepairCurrentMappings = draft.some(
    (row) => row.product !== null && mappingNeedsConfiguration(row, payload?.products ?? [], warehouseDraft),
  );
  const hasConfigurationIssue = missingColors.length > 0 || payload?.fully_configured === false;
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

  function updateWarehouse(value: string) {
    if (!canManage) return;
    const warehouseId = value ? Number(value) : null;
    setWarehouseDraft(warehouseId);
    setDraft((current) =>
      current.map((mapping) => {
        const selected = payload?.products.find((product) => product.id === mapping.product);
        if (!selected || productCanBeAssigned(selected, warehouseId)) return mapping;
        return { ...mapping, product: null, product_label: null };
      }),
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
                {hasConfigurationIssue ? (
                  <StatusChip tone="error">нужна настройка</StatusChip>
                ) : (
                  <StatusChip tone="ok">готово</StatusChip>
                )}
              </div>
            </div>
          </div>
        </div>
      </Panel>

      <Panel>
        <div className="flex items-center gap-3 px-5 py-4">
          <span className="min-w-0 flex-1">
            <SectionHead
              title="Куда приходовать"
              hint="Один раз сопоставьте распознанный цвет с товаром каталога. Подсчёт и журнал работают независимо — мешки не потеряются."
            />
          </span>
          {hasConfigurationIssue ? (
            <StatusChip tone="error">
              {missingColors.length > 0 ? `${missingColors.length} из ${draft.length}` : "проверьте"}
            </StatusChip>
          ) : (
            <StatusChip tone="ok">всё готово</StatusChip>
          )}
          <button
            type="button"
            aria-expanded={mappingOpen}
            aria-label={mappingOpen ? "Свернуть настройку прихода" : "Развернуть настройку прихода"}
            onClick={() => setMappingOpen((open) => !open)}
            className="flex size-9 shrink-0 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            <ChevronDown className={cn("size-5 transition-transform duration-200", mappingOpen && "rotate-180")} />
          </button>
        </div>

        {mappingOpen && (
          <>
            <Hairline />
            <div className="px-5 pb-5 pt-4">
              {(payload.warehouses?.length ?? 0) > 0 && (
                <label className="mb-4 grid gap-2 rounded-xl bg-slate-50 p-3 sm:grid-cols-[130px_minmax(0,1fr)] sm:items-center">
                  <span className="text-sm font-semibold text-slate-700">Склад прихода</span>
                  <Select
                    aria-label="Склад прихода"
                    value={warehouseDraft ?? ""}
                    disabled={!canManage}
                    onChange={(event) => updateWarehouse(event.target.value)}
                    className="bg-white"
                  >
                    <option value="" disabled>
                      Выберите склад
                    </option>
                    {(payload.warehouses ?? []).map((warehouse) => (
                      <option key={warehouse.id} value={warehouse.id}>
                        {warehouse.name}
                        {warehouse.is_default ? " · основной" : ""}
                      </option>
                    ))}
                  </Select>
                </label>
              )}
              <div className="space-y-2">
                {draft.map((mapping) => {
                  const meta = colorMeta(mapping.color);
                  const candidates = productOptions(payload.products, mapping.color, mapping.product, warehouseDraft);
                  const needsConfiguration = mappingNeedsConfiguration(mapping, payload.products, warehouseDraft);
                  return (
                    <label
                      key={mapping.color}
                      className="grid gap-2 sm:grid-cols-[130px_minmax(0,1fr)] sm:items-center"
                    >
                      <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-700">
                        <ColorDot className={meta.dot} /> {meta.label}
                        {needsConfiguration && (
                          <span className="rounded-md bg-red-50 px-1.5 py-0.5 text-[10px] font-bold text-red-700">
                            Не привязан
                          </span>
                        )}
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
                  <Button
                    size="sm"
                    disabled={
                      (!dirty && !canRepairCurrentMappings) ||
                      saving ||
                      ((payload.warehouses?.length ?? 0) > 0 && warehouseDraft === null)
                    }
                    onClick={() => void onSave(draft, warehouseDraft)}
                  >
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
              const destination: AlwaysOnReceiptDestination =
                row.configured && row.product_label
                  ? {
                      state: "bound",
                      productLabel: row.product_label,
                      warehouseName:
                        payload.warehouse_name ??
                        (payload.warehouse === undefined ? null : `Склад #${payload.warehouse}`),
                    }
                  : { state: "unbound" };
              return (
                <div key={row.color}>
                  <div className="flex items-center gap-2">
                    <ColorDot className={meta.dot} />
                    <span className="text-xs font-medium text-slate-500">{meta.label}</span>
                  </div>
                  <div className="mt-1.5 flex items-baseline gap-1">
                    <span className="text-3xl font-black tabular-nums tracking-tight text-slate-900">
                      {row.net_bags}
                    </span>
                    <span className="text-xs font-medium text-slate-400">меш.</span>
                  </div>
                  <AlwaysOnReceiptDestinationLabel
                    destination={destination}
                    colorLabel={meta.label}
                    className="mt-1.5"
                  />
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
                        {batch.warehouse_name ? ` · ${batch.warehouse_name}` : ""}
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
