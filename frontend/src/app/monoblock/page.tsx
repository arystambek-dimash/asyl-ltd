"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  BarChart3,
  Camera,
  CalendarDays,
  Cpu,
  Check,
  LockKeyhole,
  PackageCheck,
  RefreshCw,
  Settings2,
  ScanLine,
  ShieldCheck,
  Video,
  VideoOff,
  type LucideIcon,
} from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { CameraChoice } from "@/components/camera-choice";
import { CameraStream } from "@/components/camera-stream";
import { CameraCountingLineOverlay } from "@/components/camera-counting-line-overlay";
import { DetectionOverlay } from "@/components/detection-overlay";
import {
  AlwaysOnDayColorViewToggle,
  AlwaysOnDayRunLog,
  AlwaysOnProductionPanel,
  AlwaysOnReceiptDestinationLabel,
  buildReceiptMapping,
  receiptItemLabel,
  resolveAlwaysOnReceiptDestination,
  type AlwaysOnDayColorView,
} from "@/components/monoblock/always-on-production-panel";
import { InferredBadge } from "@/components/monoblock/unknown-color";
import { CameraAnalyticsOverview, type AnalyticsDateRange } from "@/components/monoblock/camera-analytics-overview";
import { ShippingTransportCamera } from "@/components/monoblock/shipping-transport-camera";
import { CameraShippingSessions } from "@/components/shipping/camera-shipping-sessions";
import { ShippingIdleSettings } from "@/components/shipping/shipping-idle-settings";
import { RequirePerm } from "@/components/require-perm";
import { CompletedOrdersSettingsModal } from "@/components/shipping/completed-orders-settings-modal";
import { ShippingCalendar } from "@/components/shipping/shipping-calendar";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { StatCard } from "@/components/ui/stat-card";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { ColorDot, Hairline, Metric, Panel, SectionHead, StatusChip } from "@/components/monoblock/ui";
import { brandLabel } from "@/lib/monoblock-brands";
import { colorMeta, normalizedColor } from "@/lib/monoblock-colors";
import { api, apiError } from "@/lib/api";
import { ORDER_AWAITING_SHIPMENT_STATUSES } from "@/lib/constants";
import { orderedBagCount } from "@/lib/orders";
import { normalizeVerificationLines, resolveCountingLine } from "@/lib/camera-counting-line";
import {
  cameraOwnersFor,
  indexFirstBy,
  isLogicalCamera,
  playableCameras,
  type PlayableCamera,
} from "@/lib/shipping-cameras";
import { showSuccess } from "@/lib/toast";
import { can } from "@/lib/can";
import type {
  AiCountingSession,
  AlwaysOnCameraSettings,
  AlwaysOnDailyAnalytics,
  AlwaysOnDailyCameraAnalytics,
  AlwaysOnDetection,
  AlwaysOnProcessorStatus,
  AlwaysOnProductMapping,
  AlwaysOnProductionPayload,
  AlwaysOnStockBatch,
  AlwaysOnUnknownColorInput,
  CameraContinuousReadiness,
  CameraFeed,
  MonoblockCameraSettings,
  Order,
  ShippingBoardSettings,
  ShippingCameraDayHistory,
} from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useDebounced } from "@/lib/use-debounced";
import { useLocalDay } from "@/lib/use-local-day";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { analyticsRange, cn, formatIsoDate, pluralRu } from "@/lib/utils";
import { useAuth } from "@/store/auth";

// Заказы — как на посту: 30 с слишком медленно для очереди, 10 с достаточно.
const BOARD_POLL_MS = 10_000;
const SESSION_POLL_MS = 3_000;
// Рамки тянем чаще остального: мешок пересекает кадр за секунды, и на общем
// трёхсекундном опросе рамка заметно отставала от него.
const DETECTIONS_POLL_MS = 250;
// Заказы/камеры/настройки меняются редко — не гоняем полный список заказов
// каждые 3 секунды на экране, который висит открытым весь день.
const SLOW_POLL_MS = 30_000;
const ALWAYS_ON_MODAL_VIEWS = ["live", "production", "analytics"] as const;
type ModalView = (typeof ALWAYS_ON_MODAL_VIEWS)[number] | "transport";
const SHIPPING_MODAL_VIEWS: readonly ModalView[] = ["live", "analytics", "transport"];
type MonoblockTab = "shipments" | "monoblock";

/** Заказ, занимающий камеру отгрузки, — подпись плитки. */
interface ShippingTileBinding {
  orderId: number;
  clientName: string;
  total: number;
  target: number | null;
}

const MODAL_TABS: { key: ModalView; label: string; icon: LucideIcon }[] = [
  { key: "live", label: "Прямой эфир", icon: Video },
  { key: "production", label: "Выпуск и склад", icon: PackageCheck },
  { key: "analytics", label: "Аналитика", icon: BarChart3 },
  { key: "transport", label: "Камера номера", icon: ScanLine },
];

/** Процессор камеры, которой ещё нет в ответе ПК камер: не считает и не пишет. */
function emptyProcessor(cam: string, scope: "shipping" | "ai_247"): AlwaysOnProcessorStatus {
  return { cam, running: false, mode: "always_on", analytics_scope: scope, recording: false, total: 0 };
}

function CameraSettingsButton({
  cameras,
  settings,
  reload,
}: {
  cameras: PlayableCamera[];
  settings: MonoblockCameraSettings | null;
  reload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function show() {
    setSelected(settings?.camera_sources ?? []);
    setError("");
    setOpen(true);
  }

  function toggle(source: string) {
    if ((settings?.blocked_camera_sources ?? []).includes(source)) return;
    setSelected((current) =>
      current.includes(source) ? current.filter((item) => item !== source) : [...current, source],
    );
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      await api.put("/cameras/monoblock-settings/", { camera_sources: selected });
      await reload();
      setOpen(false);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={show}>
        <Settings2 className="size-4" /> Камеры моноблока
        <span className="tabular-nums text-[var(--muted-foreground)]">{settings?.camera_sources.length ?? 0}</span>
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Настройка администратора"
        title="Камеры моноблока"
        description="Отметьте логические камеры camN. Камера-ПК подключает их напрямую к substream и готовит непрерывный AI-процессор до начала заказа."
        className="max-w-xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button disabled={saving} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить список"}
            </Button>
          </>
        }
      >
        <div className="mb-4 flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50/70 p-3 text-sm text-blue-900">
          <ShieldCheck className="mt-0.5 size-5 shrink-0 text-blue-600" />
          <p>
            Выбранные камеры остаются в отдельном контуре отгрузки и считают круглосуточно через sub. В AI 24/7 они не
            перемещаются. Камеру с активной отгрузкой нельзя добавить или убрать до завершения сессии.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {cameras.map((camera) => {
            const checked = selected.includes(camera.src);
            const blocked = (settings?.blocked_camera_sources ?? []).includes(camera.src);
            return (
              <CameraChoice
                key={camera.id}
                camera={camera}
                checked={checked}
                accent="blue"
                icon={Camera}
                disabled={blocked}
                disabledReason={blocked ? "занята контуром AI 24/7" : undefined}
                onToggle={() => toggle(camera.src)}
              />
            );
          })}
        </div>

        {!cameras.length && (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-slate-400">
            Подключённые камеры пока не обнаружены.
          </div>
        )}
        {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
      </Modal>
    </>
  );
}

function AlwaysOnSettingsButton({
  cameras,
  settings,
  onSaved,
}: {
  cameras: PlayableCamera[];
  settings: AlwaysOnCameraSettings | null;
  onSaved: (next: AlwaysOnCameraSettings) => void;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  function show() {
    setSelected(settings?.camera_sources ?? []);
    setError("");
    setOpen(true);
  }

  function toggle(source: string) {
    if ((settings?.blocked_camera_sources ?? []).includes(source)) return;
    setSelected((current) => {
      if (current.includes(source)) return current.filter((item) => item !== source);
      const activeOtherSources = settings?.active_other_camera_sources ?? [];
      const availableCapacity = settings?.capacity ? Math.max(0, settings.capacity - activeOtherSources.length) : null;
      if (availableCapacity !== null && current.length >= availableCapacity) {
        setError(
          `На ПК камер настроен общий лимит ${settings?.capacity}; активные камеры отгрузки уже занимают ${activeOtherSources.length}.`,
        );
        return current;
      }
      setError("");
      return [...current, source];
    });
  }

  async function save() {
    setSaving(true);
    setError("");
    try {
      // Ответ PUT авторитетен: сохранение в PostgreSQL уже произошло, даже
      // когда ПК цеха не ответил (202). Перечитывать список отдельным GET
      // нельзя — фоновый опрос мог стартовать до записи и вернуть прежнее
      // состояние уже после неё, из-за чего выбор «слетал» на экране.
      const { data } = await api.put<AlwaysOnCameraSettings>("/cameras/always-on-settings/", {
        camera_sources: selected,
      });
      onSaved(data);
      setOpen(false);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Button variant="outline" size="sm" onClick={show}>
        <Settings2 className="size-4" /> Настроить
        <span className="tabular-nums text-[var(--muted-foreground)]">{settings?.camera_sources.length ?? 0}</span>
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Только суперпользователь"
        title="Постоянный AI-подсчёт"
        description="Отдельный контур AI 24/7 через прямой camN/sub. Камеры отгрузки сюда не переносятся и недоступны для выбора."
        className="max-w-2xl"
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Отмена
            </Button>
            <Button disabled={saving} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Применение…" : "Применить режим"}
            </Button>
          </>
        }
      >
        <div className="mb-4 grid gap-2.5 sm:grid-cols-3">
          <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-emerald-600">Модель</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Всегда активна</p>
            {settings?.capacity && (
              <p className="mt-0.5 text-[10px] text-emerald-700/70">до {settings.capacity} камер одновременно</p>
            )}
          </div>
          <div className="rounded-2xl border border-sky-100 bg-sky-50/70 p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-sky-600">Контур</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Отдельно от отгрузки</p>
          </div>
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-3">
            <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-slate-500">Диск камеры</p>
            <p className="mt-1 text-sm font-bold text-slate-800">Технический архив 48 ч</p>
          </div>
        </div>

        {settings?.sync_status === "pending" && (
          <div className="mb-4 flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <RefreshCw className="mt-0.5 size-4 shrink-0" />
            <p>{settings.detail || "ПК камер переподключается. Настройка применится автоматически."}</p>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          {cameras.map((camera) => {
            const checked = selected.includes(camera.src);
            const blocked = (settings?.blocked_camera_sources ?? []).includes(camera.src);
            const live = settings?.processors.find((item) => item.cam === camera.src);
            return (
              <button
                key={camera.id}
                type="button"
                onClick={() => toggle(camera.src)}
                aria-pressed={checked}
                disabled={blocked}
                aria-label={blocked ? `${camera.zone}: принадлежит контуру отгрузки` : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-2xl border p-3 text-left transition",
                  checked
                    ? "border-blue-400 bg-blue-50 ring-2 ring-blue-500/15"
                    : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm",
                  blocked && "cursor-not-allowed border-amber-300 bg-amber-50/70 ring-amber-500/10",
                )}
              >
                <span
                  className={cn(
                    "flex size-11 shrink-0 items-center justify-center rounded-2xl",
                    checked ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-400",
                  )}
                >
                  <Cpu className="size-5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-bold text-slate-800">{camera.zone}</span>
                  <span className="mt-1 flex items-center gap-1.5 text-[11px] text-slate-400">
                    <span className={cn("size-1.5 rounded-full", live?.running ? "bg-emerald-400" : "bg-slate-300")} />
                    {live?.mode === "session" ? "занята отгрузкой" : live?.running ? "считает 24/7" : camera.src}
                  </span>
                  {blocked && (
                    <span className="mt-1 flex items-center gap-1 text-[10px] font-semibold text-amber-700">
                      <LockKeyhole className="size-3" /> Камера отгрузки · {camera.src}/sub
                    </span>
                  )}
                </span>
                <span
                  className={cn(
                    "flex size-7 items-center justify-center rounded-full border",
                    checked ? "border-blue-600 bg-blue-600 text-white" : "border-slate-200 text-transparent",
                  )}
                >
                  <Check className="size-4" />
                </span>
              </button>
            );
          })}
        </div>
        {!cameras.length && (
          <div className="rounded-xl border border-dashed p-8 text-center text-sm text-slate-400">
            Подключённые AI-камеры пока не обнаружены.
          </div>
        )}
        {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
      </Modal>
    </>
  );
}

function AlwaysOnCard({
  processor,
  camera,
  detail,
  readiness,
  daily,
  analyticsError,
  canManage,
  scope,
  bound,
}: {
  processor: AlwaysOnProcessorStatus;
  camera?: PlayableCamera;
  detail?: string;
  readiness?: CameraContinuousReadiness;
  daily?: AlwaysOnDailyCameraAnalytics;
  analyticsError?: string;
  canManage: boolean;
  scope: "shipping" | "ai_247";
  /** Заказ, за которым закреплена камера отгрузки (сессия или loading_camera). */
  bound?: ShippingTileBinding;
}) {
  const isShipping = scope === "shipping";
  const { me } = useAuth();
  const canManageTransport = isShipping && me?.is_superuser === true;
  const runtimeSettingsUrl = isShipping ? "/cameras/shipping-continuous-settings/" : "/cameras/always-on-settings/";
  const detectionsUrl = isShipping ? "/cameras/shipping-continuous-detections/" : "/cameras/always-on-detections/";
  const analyticsUrl = isShipping ? "/cameras/shipping-continuous-analytics/" : "/cameras/always-on-analytics/";
  const modalViews = isShipping ? SHIPPING_MODAL_VIEWS : ALWAYS_ON_MODAL_VIEWS;
  const modalPanelId = useId();
  const visibleModalTabs = MODAL_TABS.filter(
    (tab) => modalViews.includes(tab.key) && (tab.key !== "transport" || canManageTransport),
  ).map((tab) => ({ ...tab, panelId: `${modalPanelId}-${tab.key}` }));
  const modalPanelProps = (view: ModalView) => ({
    role: "tabpanel" as const,
    id: `${modalPanelId}-${view}`,
    "aria-labelledby": `${modalPanelId}-${view}-tab`,
  });
  const [open, setOpen] = useState(false);
  const today = useLocalDay();
  const [dateRange, setDateRange] = useState<AnalyticsDateRange | null>(null);
  const dateFrom = dateRange?.from ?? today;
  const dateTo = dateRange?.to ?? today;
  const { days: rangeDays, valid: rangeValid } = analyticsRange(dateFrom, dateTo);
  const rangeQuery = new URLSearchParams({ camera: processor.cam, date_from: dateFrom, date_to: dateTo }).toString();
  const [modalView, setModalView] = useState<ModalView>("live");
  const [streamOnline, setStreamOnline] = useState(false);
  // Рамки модели можно скрыть: иногда оператору нужно посмотреть на сам кадр.
  const [showDetections, setShowDetections] = useState(true);
  // Рамки живут отдельно от остального состояния: их опрашиваем чаще, чтобы
  // они держались мешка, и помечаем временем — устаревшие гасим.
  const [liveBoxes, setLiveBoxes] = useState<{
    detections: AlwaysOnDetection[];
    bagsPresent?: boolean | null;
    frame?: { width?: number; height?: number } | null;
    line?: AlwaysOnProcessorStatus["line"];
    direction?: AlwaysOnProcessorStatus["direction"];
    revision?: string | null;
    at: number;
  } | null>(null);
  const [liveProcessor, setLiveProcessor] = useState(processor);
  const [liveReadiness, setLiveReadiness] = useState(readiness);
  const [liveDaily, setLiveDaily] = useState<AlwaysOnDailyCameraAnalytics | undefined>(daily);
  const [liveDetail, setLiveDetail] = useState(detail || "");
  const [liveAnalyticsError, setLiveAnalyticsError] = useState(analyticsError || "");
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [loadedAnalyticsQuery, setLoadedAnalyticsQuery] = useState<string | null>(null);
  const [analyticsReload, setAnalyticsReload] = useState(0);
  const [production, setProduction] = useState<AlwaysOnProductionPayload | null>(null);
  const [productionLoading, setProductionLoading] = useState(false);
  const [productionError, setProductionError] = useState<string | null>(null);
  const [productionSaving, setProductionSaving] = useState(false);
  const productionRequestSequence = useRef(0);
  const productionMutationInFlight = useRef(false);
  const [selectedDayHistory, setSelectedDayHistory] = useState<
    AlwaysOnProductionPayload | ShippingCameraDayHistory | null
  >(null);
  const selectedProductionDay = selectedDayHistory && "mappings" in selectedDayHistory ? selectedDayHistory : null;
  const [selectedProductionLoading, setSelectedProductionLoading] = useState(false);
  const [selectedProductionError, setSelectedProductionError] = useState<string | null>(null);
  const [selectedProductionReload, setSelectedProductionReload] = useState(0);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const dayDetailHeading = useRef<HTMLHeadingElement>(null);
  const dayTrigger = useRef<HTMLElement | null>(null);
  const [selectedDayColorView, setSelectedDayColorView] = useState<AlwaysOnDayColorView>("algorithm");
  const current = open ? liveProcessor : processor;
  const currentReadiness = open ? liveReadiness : readiness;
  const bagsPresent = open && liveBoxes ? liveBoxes.bagsPresent : current.bags_present;
  // Рамки на видео и их число на кнопке — из одного источника: частого опроса, пока он есть.
  const visibleDetections = liveBoxes ? liveBoxes.detections : current.detections;
  const countingLine = resolveCountingLine(
    {
      line: liveBoxes?.line ?? current.line,
      direction: liveBoxes?.direction ?? current.direction,
    },
    camera?.line_config,
  );
  const rangeMatches = liveDaily?.date_from === dateFrom && liveDaily?.date_to === dateTo;
  const currentDaily = open ? (rangeValid && rangeMatches ? liveDaily : undefined) : daily;
  const todayTotal = currentDaily?.total ?? 0;
  const allTimeTotal = currentDaily?.all_time_total ?? todayTotal;
  const analyticsTransportError = open ? liveAnalyticsError : analyticsError;
  const analyticsAvailable = !analyticsTransportError && currentDaily?.analytics_sync?.available === true;
  const analyticsDetail =
    analyticsTransportError || currentDaily?.analytics_sync?.detail || "Аналитика событий ещё не синхронизирована";
  const todayDisplay = analyticsAvailable ? todayTotal : "—";
  const allTimeDisplay = analyticsAvailable ? allTimeTotal : "—";
  const liveCounterAvailable =
    current.running === true &&
    current.processor_alive === true &&
    current.source === "sub" &&
    current.analytics_scope === scope &&
    currentReadiness?.status === "synced" &&
    Number.isFinite(current.total) &&
    current.total >= 0;
  const currentCycleDisplay = liveCounterAvailable ? current.total : "—";
  const inSession = current.mode === "session";
  const chartMax = Math.max(1, ...(currentDaily?.history ?? []).map((item) => item.total));
  // «Куда приходовать» — одна привязка и для обзора, и для разбора выбранного дня.
  const receiptMapping = useMemo(
    () => buildReceiptMapping(selectedProductionDay, production, selectedProductionError || productionError),
    [production, productionError, selectedProductionDay, selectedProductionError],
  );
  // Разбор одного дня: сам столбик уже несёт полную статистику, поэтому
  // выбранный день хранится ключом, а не копией — опрос обновляет данные,
  // не закрывая панель.
  const selectedPoint = (currentDaily?.history ?? []).find((item) => item.day === selectedDay);
  function selectAnalyticsDay(day: string | null) {
    if (day) dayTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setSelectedDay(day);
    if (!day) dayTrigger.current?.focus();
  }
  useEffect(() => {
    if (selectedPoint?.day) {
      const heading = dayDetailHeading.current;
      heading?.focus({ preventScroll: true });
      const scrollBody = heading?.closest<HTMLElement>("[data-modal-scroll-body]");
      if (heading && scrollBody) {
        // Scroll only the body: scrollIntoView also moves overflow-hidden
        // ancestors and can push the modal title/tabs off screen on mobile.
        scrollBody.scrollTop = Math.max(
          0,
          scrollBody.scrollTop + heading.getBoundingClientRect().top - scrollBody.getBoundingClientRect().top - 80,
        );
      }
    }
  }, [selectedPoint?.day]);
  // Разбивку за день считает бэкенд — тем же кодом, что и общую, поэтому
  // цифры сходятся.
  const selectedColors = selectedPoint?.colors ?? [];
  // Дневная детализация приходит отдельным запросом. Проверка даты не даёт
  // на один рендер показать ответ предыдущего столбика после быстрого клика.
  const selectedHistory = selectedDayHistory?.selected_day === selectedPoint?.day ? selectedDayHistory : null;
  const selectedRawRuns = selectedHistory?.day_runs ?? null;
  const selectedAlgorithmRuns = selectedHistory?.algorithm_day_runs ?? null;
  const smoothing = selectedHistory?.run_smoothing;
  const historyComplete =
    selectedHistory && ("history_status" in selectedHistory ? selectedHistory.history_status === "complete" : true);
  const rawRunsTotal = selectedRawRuns?.reduce((sum, run) => sum + run.model_bags, 0);
  const runsMatchSelectedAnalytics = Boolean(
    selectedPoint &&
    historyComplete &&
    !selectedProductionError &&
    selectedRawRuns &&
    !selectedRawRuns.some((run) => run.is_partial_for_day) &&
    rawRunsTotal === selectedPoint.model_total &&
    smoothing?.raw_model_total === selectedPoint.model_total &&
    smoothing.algorithm_model_total === selectedPoint.model_total,
  );
  const selectedVisibleRuns = selectedHistory
    ? runsMatchSelectedAnalytics
      ? selectedDayColorView === "algorithm"
        ? selectedAlgorithmRuns
        : selectedRawRuns
      : []
    : null;
  const runMismatchMessage =
    selectedHistory && "history_status" in selectedHistory && !historyComplete
      ? selectedHistory.history_detail
      : selectedHistory && !runsMatchSelectedAnalytics
        ? "Периоды недоступны: журнал не совпадает с итогом выбранного дня."
        : null;
  // Старые интервалы могут пересекать границу дня, а append-only журнал —
  // границу переноса в архив. В обоих случаях не смешиваем разные срезы.
  const selectedVisibleColors =
    runsMatchSelectedAnalytics && smoothing
      ? selectedDayColorView === "algorithm"
        ? smoothing.algorithm_colors
        : smoothing.raw_colors
      : selectedColors;
  const selectedBrandsByColor = selectedHistory?.dominant_brand_by_color;
  const selectedBrandByColor = new Map(
    Object.entries(selectedBrandsByColor ?? {}).map(([color, brand]) => [normalizedColor(color), brand]),
  );
  const selectedBrandStatus = selectedBrandsByColor
    ? "ready"
    : selectedProductionError || selectedHistory
      ? "unavailable"
      : "loading";
  useEffect(() => {
    if (open) return;
    setLiveProcessor(processor);
    setLiveReadiness(readiness);
    setLiveDaily(daily);
    setLiveDetail(detail || "");
    setLiveAnalyticsError(analyticsError || "");
  }, [analyticsError, daily, detail, open, processor, readiness]);

  useEffect(() => {
    setSelectedDay(null);
  }, [rangeQuery]);

  // Разбор дня — состояние одного просмотра: закрыли окно, выбор снят.
  useEffect(() => {
    if (!open) setSelectedDay(null);
  }, [open]);

  useEffect(() => {
    setSelectedDayColorView("algorithm");
  }, [selectedDay]);

  useVisiblePolling(
    async ({ signal, first }) => {
      if (first) {
        setLiveAnalyticsError("");
        setAnalyticsLoading(true);
      }
      try {
        const [settingsResponse, analyticsResponse] = await Promise.all([
          api.get<AlwaysOnCameraSettings>(runtimeSettingsUrl, { signal }),
          api.get<AlwaysOnDailyAnalytics>(`${analyticsUrl}?${rangeQuery}`, { signal }),
        ]);
        if (signal.aborted) return;
        const next = settingsResponse.data.processors.find((item) => item.cam === processor.cam);
        setLiveProcessor(next ?? emptyProcessor(processor.cam, scope));
        setLiveReadiness(settingsResponse.data.camera_readiness?.[processor.cam]);
        setLiveDaily(analyticsResponse.data.cameras.find((item) => item.camera === processor.cam));
        setLiveDetail(continuousCameraDetail(settingsResponse.data, processor.cam));
        setLiveAnalyticsError("");
      } catch (cause) {
        if (!signal.aborted) {
          const message = apiError(cause);
          setLiveDetail(message);
          setLiveAnalyticsError(message);
        }
      } finally {
        if (!signal.aborted) {
          setAnalyticsLoading(false);
          setLoadedAnalyticsQuery(rangeQuery);
        }
      }
    },
    SESSION_POLL_MS,
    open && rangeValid,
    {
      immediate: true,
      resetKey: `${analyticsReload}|${analyticsUrl}|${processor.cam}|${rangeQuery}|${runtimeSettingsUrl}|${scope}`,
    },
  );

  // Быстрый опрос только рамок. Отдельно от тяжёлого снимка: аналитику и
  // настройки незачем перечитывать раз в секунду, а рамка на общем интервале
  // отставала от мешка и висела после его ухода.
  const detectionsActive = open && modalView === "live" && showDetections;
  useEffect(() => {
    if (!detectionsActive) setLiveBoxes(null);
  }, [detectionsActive]);
  useVisiblePolling(
    async ({ signal }) => {
      try {
        const { data } = await api.get<{ processors: AlwaysOnProcessorStatus[] }>(detectionsUrl, { signal });
        if (signal.aborted) return;
        const row = data.processors.find((item) => item.cam === processor.cam);
        const revision = row?.last_frame_at ?? (row ? null : "processor-missing");
        setLiveBoxes((previous) => {
          // Four quick browser polls can hit the same one-second backend
          // snapshot. Do not rerender the whole card until that snapshot (or
          // its applied line) actually changes.
          if (
            revision &&
            revision === previous?.revision &&
            row?.line === previous.line &&
            row?.direction === previous.direction &&
            row?.bags_present === previous.bagsPresent
          ) {
            return previous;
          }
          return {
            // A successful snapshot without this processor is authoritative.
            // An explicit empty list prevents the initial, now-stale settings
            // snapshot from reappearing through a nullish fallback.
            detections: row?.detections ?? [],
            bagsPresent: row?.bags_present ?? null,
            frame: row?.detection_frame,
            line: row?.line,
            direction: row?.direction,
            revision,
            at: Date.now(),
          };
        });
      } catch {
        // Null means that the first fast poll has not completed yet. Once a
        // poll fails, keep an explicit empty snapshot so initial detections do
        // not reappear behind an unavailable endpoint.
        if (!signal.aborted) {
          setLiveBoxes((previous) =>
            previous?.revision === "unavailable"
              ? previous
              : { detections: [], bagsPresent: null, revision: "unavailable", at: Date.now() },
          );
        }
      }
    },
    DETECTIONS_POLL_MS,
    detectionsActive,
    { immediate: true, resetKey: `${detectionsUrl}|${processor.cam}` },
  );

  const loadProduction = useCallback(
    async (showLoader = false, signal?: AbortSignal) => {
      if (productionMutationInFlight.current) return null;
      const requestSequence = ++productionRequestSequence.current;
      if (showLoader) setProductionLoading(true);
      setProductionError(null);
      try {
        const response = await api.get<AlwaysOnProductionPayload>(
          `/cameras/always-on-production/?camera=${encodeURIComponent(processor.cam)}`,
          { signal },
        );
        if (
          signal?.aborted ||
          requestSequence !== productionRequestSequence.current ||
          productionMutationInFlight.current
        ) {
          return null;
        }
        setProduction(response.data);
        return response.data;
      } catch (cause) {
        if (
          !signal?.aborted &&
          requestSequence === productionRequestSequence.current &&
          !productionMutationInFlight.current
        ) {
          setProductionError(apiError(cause));
        }
        return null;
      } finally {
        if (requestSequence === productionRequestSequence.current) {
          setProductionLoading(false);
        }
      }
    },
    [processor.cam],
  );

  // Both views use the same snapshot. A new view or camera aborts the reply
  // in flight; only the production tab shows the loader on its first request.
  useVisiblePolling(
    ({ signal, first }) => loadProduction(first && modalView === "production", signal),
    15_000,
    open && (modalView === "production" || (!isShipping && modalView === "analytics" && !selectedDay)),
    { immediate: true, resetKey: `${modalView}|${processor.cam}` },
  );

  // Исторический день запрашиваем отдельно: полный ответ вкладки «Выпуск и
  // склад» нельзя подменять дневным срезом. Текущий выбранный день обновляем,
  // пока окно открыто — так строка «идёт сейчас» и количество не замирают.
  const selectedDayActive = open && modalView === "analytics" && !!selectedDay;
  const pollCurrentDay = selectedDay === currentDaily?.day;
  useEffect(() => {
    if (selectedDayActive) return;
    setSelectedDayHistory(null);
    setSelectedProductionError(null);
    setSelectedProductionLoading(false);
  }, [selectedDayActive]);
  useVisiblePolling(
    async ({ signal, first }) => {
      // Прошедший день не меняется: он грузится один раз, опрашивается только текущий.
      if (!selectedDay || (!first && !pollCurrentDay)) return;
      if (first) {
        setSelectedDayHistory(null);
        setSelectedProductionLoading(true);
        setSelectedProductionError(null);
      }
      try {
        const params = new URLSearchParams({ camera: processor.cam, day: selectedDay });
        const response = isShipping
          ? await api.get<ShippingCameraDayHistory>(`/cameras/shipping-continuous-history/?${params}`, { signal })
          : await api.get<AlwaysOnProductionPayload>(`/cameras/always-on-production/?${params}`, { signal });
        if (signal.aborted) return;
        setSelectedProductionError(null);
        setProductionError(null);
        setSelectedDayHistory(response.data);
      } catch (cause) {
        if (!signal.aborted) setSelectedProductionError(apiError(cause));
      } finally {
        if (!signal.aborted) setSelectedProductionLoading(false);
      }
    },
    15_000,
    selectedDayActive,
    {
      immediate: true,
      resetKey: `${currentDaily?.day}|${isShipping}|${processor.cam}|${selectedDay}|${selectedProductionReload}`,
    },
  );

  // Пока идёт запись, опрос вкладки молчит, а ответ уже отправленного GET
  // отбрасывается по номеру запроса: старый снимок не перетрёт новый.
  async function runProductionMutation<T>(mutate: () => Promise<T>): Promise<T> {
    productionMutationInFlight.current = true;
    productionRequestSequence.current += 1;
    setProductionLoading(false);
    try {
      return await mutate();
    } finally {
      productionMutationInFlight.current = false;
    }
  }

  async function saveProductionMappings(mappings: AlwaysOnProductMapping[], warehouse: number | null) {
    if (!canManage) return;
    setProductionSaving(true);
    setProductionError(null);
    try {
      const response = await runProductionMutation(() =>
        api.put<AlwaysOnProductionPayload>("/cameras/always-on-production/", {
          camera: processor.cam,
          ...(warehouse !== null ? { warehouse } : {}),
          mappings: mappings.map(({ color, product }) => ({ color, product })),
        }),
      );
      setProduction(response.data);
      showSuccess("Привязки цветов к товарам сохранены");
    } catch (cause) {
      setProductionError(apiError(cause));
    } finally {
      setProductionSaving(false);
    }
  }

  // «Указать цвет» мешкам без цвета. Ответ — свежий снимок вкладки: применяем
  // его сразу, чтобы опрос не показал старое число. Ошибку показывает окно.
  async function assignUnknownColor(input: AlwaysOnUnknownColorInput) {
    if (!canManage) return;
    const response = await runProductionMutation(() =>
      api.post<AlwaysOnProductionPayload>("/cameras/always-on-production/unknown-colors/", {
        camera: processor.cam,
        ...input,
      }),
    );
    setProduction(response.data);
    setProductionError(null);
    showSuccess("Цвет указан");
  }

  async function retryProductionBatch(batch: AlwaysOnStockBatch) {
    if (!canManage) return;
    setProductionError(null);
    try {
      await runProductionMutation(() => api.post(`/cameras/always-on-production/batches/${batch.id}/retry/`));
      await loadProduction(false);
      showSuccess("Приёмка повторно проверена");
    } catch (cause) {
      setProductionError(apiError(cause));
    }
  }

  function showStream() {
    setStreamOnline(false);
    setModalView("live");
    setOpen(true);
  }

  function closeStream() {
    setOpen(false);
    setStreamOnline(false);
  }

  // Состояние плитки: обрыв сигнала важнее режима, режим — важнее фона.
  const tileStatus =
    camera?.online === false
      ? { dot: "bg-[var(--destructive)]", label: "нет сигнала" }
      : inSession || bound
        ? { dot: "bg-[var(--ring)]", label: "идёт погрузка" }
        : current.running
          ? { dot: "bg-[var(--success)]", label: isShipping ? "фоновый подсчёт" : "считает 24/7" }
          : { dot: "bg-[var(--warning)]", label: "переподключение" };
  // Вторая строка плитки: камере отгрузки важен занявший её заказ, камере
  // AI 24/7 — есть ли мешки в кадре прямо сейчас.
  const tileDetail = isShipping
    ? bound
      ? `#${bound.orderId} · ${bound.clientName} · ${bound.total}/${bound.target ?? "—"}`
      : "свободна"
    : `Мешки в кадре: ${bagsPresent === true ? "есть" : bagsPresent === false ? "нет" : "нет данных"}`;

  return (
    <>
      <button
        type="button"
        onClick={showStream}
        aria-label={`Открыть прямой эфир камеры ${camera?.zone || processor.cam}`}
        className="relative block aspect-[16/7] w-full overflow-hidden rounded-lg bg-[#141416] text-left focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50 xl:aspect-video"
      >
        <span className="absolute left-3 top-3 flex items-center gap-1.5 text-[11px] text-white/80">
          <span className={cn("size-2 rounded-full", tileStatus.dot)} />
          {tileStatus.label}
        </span>
        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-right">
          <span className="block text-[28px] font-semibold leading-none tabular-nums text-white">{todayDisplay}</span>
          <span className="mt-1 block text-[11px] text-white/60">сегодня</span>
        </span>
        <span className="absolute inset-x-0 bottom-0 px-3 pb-2.5">
          <span className="block truncate text-[14px] font-medium text-white">{camera?.zone || processor.cam}</span>
          <span className="block truncate text-[12px] text-white/70">{tileDetail}</span>
        </span>
      </button>

      <Modal
        open={open}
        onClose={closeStream}
        eyebrow={isShipping ? "Отгрузки · камеры работают 24/7" : "AI 24/7 · мониторинг"}
        title={camera?.zone || processor.cam}
        description={
          isShipping
            ? "Прямой эфир и учёт мешков при погрузке."
            : "Прямой эфир, выпуск продукции и поступления на склад."
        }
        className="max-w-5xl"
        mobileFullscreen
      >
        <div className="sticky -top-4 z-20 -mx-4 -mt-4 mb-5 border-b border-[var(--border)] bg-[var(--card)] px-4 py-3 sm:-top-6 sm:-mx-6 sm:-mt-6 sm:px-6">
          <Tabs
            tabs={visibleModalTabs}
            active={modalView}
            onChange={(key) => setModalView(key as ModalView)}
            variant="segment"
            label="Режим мониторинга камеры"
            className="w-full max-w-full overflow-x-auto sm:w-fit [&>button]:min-w-0 [&>button]:flex-1 [&>button]:whitespace-nowrap [&>button]:px-2 [&>button]:text-xs sm:[&>button]:flex-none sm:[&>button]:px-4 sm:[&>button]:text-sm [&>button>svg]:hidden sm:[&>button>svg]:block"
          />
        </div>

        {modalView === "live" ? (
          <div
            {...modalPanelProps("live")}
            className="grid overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--card)] lg:grid-cols-[minmax(0,1fr)_260px]"
          >
            {/* Видео остаётся тёмным, как плитки: на чёрном кадре рамки и линия читаются лучше. */}
            <div className="relative aspect-video min-h-0 overflow-hidden bg-[#141416] lg:aspect-auto lg:min-h-[460px]">
              {camera?.src ? (
                <CameraStream
                  src={camera.src}
                  onStateChange={setStreamOnline}
                  className="absolute inset-0 size-full object-contain"
                />
              ) : null}
              {/* Всегда-включённый поток идёт без вжатых рамок и линии,
                  поэтому весь слой модели собирает браузер поверх видео. */}
              {streamOnline && showDetections && (
                <>
                  <DetectionOverlay
                    detections={visibleDetections}
                    frame={liveBoxes?.frame ?? current.detection_frame}
                    updatedAt={liveBoxes?.at}
                  />
                  {countingLine && (
                    <CameraCountingLineOverlay
                      line={countingLine.line}
                      direction={countingLine.direction}
                      verificationLines={normalizeVerificationLines(camera?.line_config?.verification_lines)}
                    />
                  )}
                </>
              )}
              {!streamOnline && (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-[#141416] text-white/45">
                  <VideoOff className="size-8" />
                  <span className="text-sm">Подключаем прямой поток…</span>
                </div>
              )}
              <div className="absolute left-2.5 top-2.5 flex items-center gap-2 rounded-full border border-white/15 bg-black/45 px-2.5 py-1 text-[11px] font-medium text-white backdrop-blur-md sm:left-4 sm:top-4 sm:px-3 sm:py-1.5 sm:text-xs">
                <span
                  className={cn("size-2 rounded-full", streamOnline ? "bg-[var(--success)]" : "bg-[var(--warning)]")}
                />
                {streamOnline ? "В эфире" : "Подключение"}
              </div>
              {streamOnline && (
                <button
                  type="button"
                  onClick={() => setShowDetections((current) => !current)}
                  aria-pressed={showDetections}
                  className={cn(
                    "absolute right-2.5 top-2.5 flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium backdrop-blur-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] sm:right-4 sm:top-4 sm:px-3 sm:py-1.5 sm:text-xs",
                    showDetections
                      ? "border-[var(--success)]/50 bg-[var(--success)]/25 text-white"
                      : "border-white/15 bg-black/45 text-white/70",
                  )}
                >
                  <ScanLine className="size-3.5" />
                  {showDetections ? "Рамки и линия" : "Слой скрыт"}
                  {showDetections && visibleDetections?.length ? (
                    <span className="tabular-nums">· {visibleDetections.length}</span>
                  ) : null}
                </button>
              )}
            </div>

            <aside className="flex flex-col justify-between gap-5 border-t border-[var(--border)] p-4 sm:p-5 lg:border-l lg:border-t-0">
              <div>
                <Metric
                  label={
                    <span className="inline-flex items-center gap-1.5">
                      <CalendarDays className="size-3.5" /> Реальный итог за сегодня
                    </span>
                  }
                  value={todayDisplay}
                  unit={analyticsAvailable ? "меш." : undefined}
                />
                <div className="mt-1 text-[12px] text-[var(--muted-foreground)]">
                  {analyticsAvailable ? "накоплено CRM" : "аналитика не синхронизирована"}
                </div>

                <div className="mt-4 grid grid-cols-2 gap-x-4 text-[13px] sm:mt-6 sm:block">
                  <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
                    <span className="text-[var(--muted-foreground)]">За всё время</span>
                    <span className="font-medium tabular-nums text-[var(--foreground)]">{allTimeDisplay}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
                    <span className="text-[var(--muted-foreground)]">Текущий цикл</span>
                    <span className="font-medium tabular-nums text-[var(--foreground)]">{currentCycleDisplay}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
                    <span className="text-[var(--muted-foreground)]">Модель</span>
                    <StatusChip tone={current.running ? "ok" : "warn"}>
                      {current.running ? "работает" : "ожидает связь"}
                    </StatusChip>
                  </div>
                  <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
                    <span className="text-[var(--muted-foreground)]">Режим</span>
                    <span className="font-medium text-[var(--foreground)]">{inSession ? "отгрузка" : "24/7"}</span>
                  </div>
                  {(currentDaily?.adjustment ?? 0) < 0 && (
                    <div className="col-span-2 flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
                      <span className="text-[var(--muted-foreground)]">Корректировка</span>
                      <span className="font-medium tabular-nums text-[var(--warning)]">{currentDaily?.adjustment}</span>
                    </div>
                  )}
                </div>
              </div>
              {(!analyticsAvailable || current.error || liveDetail) && (
                <p className="rounded-md border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2.5 text-[12px] leading-relaxed text-[var(--foreground)]">
                  {!analyticsAvailable ? analyticsDetail : current.error || liveDetail}
                </p>
              )}
            </aside>
          </div>
        ) : modalView === "transport" && canManageTransport ? (
          <div {...modalPanelProps("transport")}>
            {open && <ShippingTransportCamera conveyorCamera={processor.cam} />}
          </div>
        ) : modalView === "production" ? (
          <div {...modalPanelProps("production")}>
            <AlwaysOnProductionPanel
              payload={production}
              loading={productionLoading}
              error={productionError}
              saving={productionSaving}
              canManage={canManage}
              onSave={saveProductionMappings}
              onRetry={retryProductionBatch}
              onAssignUnknown={assignUnknownColor}
            />
          </div>
        ) : modalView === "analytics" ? (
          <div {...modalPanelProps("analytics")} className="space-y-4">
            <CameraAnalyticsOverview
              today={today}
              dateFrom={dateFrom}
              dateTo={dateTo}
              onRangeChange={setDateRange}
              daily={currentDaily}
              loading={rangeValid && (analyticsLoading || loadedAnalyticsQuery !== rangeQuery)}
              available={analyticsAvailable}
              error={analyticsDetail}
              onRetry={() => setAnalyticsReload((value) => value + 1)}
              isShipping={isShipping}
              receiptMapping={receiptMapping}
              selectedDay={selectedDay}
              onSelectDay={selectAnalyticsDay}
            />

            {selectedPoint && (
              <Panel className="p-5 sm:p-6">
                <div className="flex flex-wrap items-center gap-2">
                  <h4
                    ref={dayDetailHeading}
                    tabIndex={-1}
                    className="scroll-mt-20 text-[15px] font-semibold tracking-tight outline-none"
                  >
                    {formatIsoDate(selectedPoint.day)}
                  </h4>
                  <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                    <AlwaysOnDayColorViewToggle
                      view={selectedDayColorView}
                      nMin={smoothing?.n_min ?? 10}
                      disabled={!runsMatchSelectedAnalytics || !!selectedProductionError}
                      onChange={setSelectedDayColorView}
                    />
                    <Button variant="ghost" size="sm" onClick={() => selectAnalyticsDay(null)}>
                      Закрыть
                    </Button>
                  </div>
                </div>

                <div className="mt-4 grid max-w-xl grid-cols-2 gap-x-8 gap-y-4">
                  <Metric label="Учтено за день" value={selectedPoint.total} size="sm" />
                  {rangeDays > 1 && (
                    <Metric
                      label="От максимума"
                      value={`${Math.round((selectedPoint.total * 100) / chartMax)}%`}
                      size="sm"
                    />
                  )}
                </div>

                {selectedVisibleColors.length > 0 && (
                  <>
                    <Hairline className="my-5" />
                    <SectionHead
                      title={isShipping ? "Цвета мешков за день" : "Цвета и продукция за день"}
                      hint={
                        isShipping
                          ? "Отдельная аналитика камеры отгрузки; эти данные не создают выпуск или приход на склад."
                          : "Количество по цветам распознано камерой; товар показан по текущему сопоставлению в разделе «Куда приходовать»."
                      }
                    />
                    <div className="mt-3 grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2 xl:grid-cols-3">
                      {selectedVisibleColors.map((item) => {
                        if (isShipping) {
                          return (
                            <div
                              key={item.color}
                              role="group"
                              aria-label={`${colorMeta(item.color).label}: ${item.total} мешков`}
                            >
                              <div className="flex items-center gap-2">
                                <ColorDot className={colorMeta(item.color).dot} />
                                <span className="min-w-0 truncate text-[12px] font-medium text-[var(--foreground)]">
                                  {colorMeta(item.color).label}
                                </span>
                                <span className="ml-auto text-[12px] tabular-nums text-[var(--muted-foreground)]">
                                  {item.percent}%
                                </span>
                              </div>
                              <Metric value={item.total} size="sm" className="mt-1" />
                            </div>
                          );
                        }
                        const brand = selectedBrandByColor.get(normalizedColor(item.color));
                        const brandText = brand
                          ? brandLabel(brand)
                          : selectedBrandStatus === "ready"
                            ? "Бренд не определён"
                            : selectedBrandStatus === "unavailable"
                              ? "Бренд недоступен"
                              : "Загрузка бренда…";
                        const colorAndBrandLabel = `${colorMeta(item.color).label} · ${brandText}`;
                        const destination = resolveAlwaysOnReceiptDestination(receiptMapping, item.color);
                        const hasProduct = destination.state === "bound";
                        const { title: itemLabel, colorLabel } = receiptItemLabel(destination, item.color);
                        return (
                          <div key={item.color} role="group" aria-label={`${itemLabel}: ${item.total} мешков`}>
                            <div className="flex min-w-0 items-start gap-2">
                              <AlwaysOnReceiptDestinationLabel destination={destination} colorLabel={colorLabel} />
                              <span className="ml-auto text-[12px] tabular-nums text-[var(--muted-foreground)]">
                                {item.percent}%
                              </span>
                            </div>
                            <Metric value={item.total} size="sm" className="mt-1" />
                            <InferredBadge inferred={item.inferred} className="mt-1" />
                            {normalizedColor(item.color) === "unknown" && canManage && (
                              <button
                                type="button"
                                onClick={() => setModalView("production")}
                                className="mt-1 block text-[11px] font-semibold text-[var(--primary)] hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/40"
                              >
                                Указать цвет — в «Выпуск и склад»
                              </button>
                            )}
                            <div className="mt-1.5 flex min-w-0 items-center gap-2">
                              <ColorDot className={colorMeta(item.color).dot} />
                              <span
                                title={hasProduct ? (brand ? `Бренд: ${brandText}` : brandText) : colorAndBrandLabel}
                                className={cn(
                                  "truncate text-[11px] font-medium",
                                  brand ? "text-[var(--foreground)]" : "text-[var(--muted-foreground)]",
                                )}
                              >
                                {hasProduct ? (brand ? `Бренд: ${brandText}` : brandText) : colorAndBrandLabel}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}

                <Hairline className="my-5" />
                <AlwaysOnDayRunLog
                  day={selectedPoint.day}
                  runs={selectedVisibleRuns}
                  timezone={selectedHistory?.timezone || "Asia/Almaty"}
                  loading={selectedProductionLoading}
                  error={selectedProductionError}
                  unavailableReason={runMismatchMessage}
                  receiptMapping={isShipping ? undefined : receiptMapping}
                  onRetry={() => setSelectedProductionReload((value) => value + 1)}
                />
              </Panel>
            )}

            {isShipping && rangeValid && (
              <CameraShippingSessions
                camera={processor.cam}
                day={rangeDays === 1 ? dateFrom : selectedDay}
                today={today}
              />
            )}
          </div>
        ) : null}
      </Modal>
    </>
  );
}

/** Пояснение для камеры: своё из готовности камеры, иначе общее — одинаково для плитки и открытого окна. */
function continuousCameraDetail(settings: AlwaysOnCameraSettings, cam: string): string {
  return settings.camera_readiness?.[cam]?.detail || settings.detail || "";
}

/** Плитка непрерывной камеры (отгрузка или AI 24/7): без стрима, тап открывает модалку AlwaysOnCard. */
function ContinuousCameraTile({
  scope,
  source,
  settings,
  analytics,
  analyticsError,
  camera,
  bound,
  canManage = false,
}: {
  scope: "shipping" | "ai_247";
  source: string;
  settings: AlwaysOnCameraSettings;
  analytics: AlwaysOnDailyAnalytics | null;
  analyticsError: string;
  camera?: PlayableCamera;
  bound?: ShippingTileBinding;
  canManage?: boolean;
}) {
  const processor = settings.processors.find((item) => item.cam === source) ?? emptyProcessor(source, scope);
  return (
    <AlwaysOnCard
      scope={scope}
      processor={processor}
      camera={camera}
      detail={continuousCameraDetail(settings, source)}
      readiness={settings.camera_readiness?.[source]}
      daily={analytics?.cameras.find((item) => item.camera === source)}
      analyticsError={analyticsError}
      canManage={canManage}
      bound={bound}
    />
  );
}

function MonoblockPageInner() {
  const { me } = useAuth();
  // Моноблок только для просмотра: одно право видит всё (грузовики, вагоны, AI 24/7).
  // Отгружает грузчик на своей странице; режим AI 24/7 и «Куда приходовать» —
  // только суперпользователь.
  const canManage = can(me, "sys_permissions.manage");
  const canManageAlwaysOn = !!me?.is_superuser;
  const canOpenOrder = can(me, "orders.view");

  // Доска живёт сегодняшним днём; другой день и поиск — явный выбор
  // оператора. URL без фильтров остаётся ровно "/orders/?post_board=1".
  // Сегодняшняя дата хранится как '' (календарь сам так её отдаёт): иначе
  // после полуночи доска застыла бы на вчерашнем дне, выбранном явно.
  const today = useLocalDay();
  const [day, setDay] = useState("");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search.trim(), 300);
  const boardQuery = useMemo(() => {
    let query = "";
    if (day && day !== today) query += `&day=${day}`;
    if (debouncedSearch) query += `&search=${encodeURIComponent(debouncedSearch)}`;
    return query;
  }, [day, debouncedSearch, today]);

  const { data: orders, error, reload: reloadOrders } = useApi<Order[]>(`/orders/?post_board=1${boardQuery}`);
  const {
    data: sessions,
    error: sessionsError,
    reload: reloadSessions,
  } = useApi<AiCountingSession[]>("/cameras/ai/sessions/");
  const { data: cameras, error: camerasError, reload: reloadCameras } = useApi<CameraFeed[]>("/cameras/");
  // Выбор камер моноблока нужен только администратору: GET ходит на ПК камер.
  const {
    data: cameraSettings,
    error: cameraSettingsError,
    reload: reloadCameraSettings,
  } = useApi<MonoblockCameraSettings>(canManage ? "/cameras/monoblock-settings/" : null);
  const {
    data: shippingSettings,
    error: shippingSettingsError,
    reload: reloadShippingSettings,
    setData: setShippingSettings,
  } = useApi<ShippingBoardSettings>("/cameras/shipping-settings/");
  const {
    data: alwaysOnSettings,
    error: alwaysOnSettingsError,
    reload: reloadAlwaysOnSettings,
    setData: setAlwaysOnSettings,
  } = useApi<AlwaysOnCameraSettings>("/cameras/always-on-settings/");
  const {
    data: alwaysOnAnalytics,
    error: alwaysOnAnalyticsError,
    reload: reloadAlwaysOnAnalytics,
  } = useApi<AlwaysOnDailyAnalytics>("/cameras/always-on-analytics/");
  const {
    data: shippingContinuousSettings,
    error: shippingContinuousSettingsError,
    reload: reloadShippingContinuousSettings,
  } = useApi<AlwaysOnCameraSettings>("/cameras/shipping-continuous-settings/");
  const {
    data: shippingContinuousAnalytics,
    error: shippingContinuousAnalyticsError,
    reload: reloadShippingContinuousAnalytics,
  } = useApi<AlwaysOnDailyAnalytics>("/cameras/shipping-continuous-analytics/");

  // Страница разделена на вкладки: «Отгрузка» (по умолчанию) — очередь и
  // камеры отгрузки, «AI 24/7» — сам моноблок с бесконечным циклом подсчёта.
  const [tab, setTab] = useState<MonoblockTab>("shipments");
  const [shippingTab, setShippingTab] = useState("conveyors");
  const shippingPanelId = useId();
  const [transportType, setTransportType] = useState<"truck" | "train">("truck");
  const [completedOpen, setCompletedOpen] = useState(false);

  const allPlayable = useMemo(() => playableCameras(cameras), [cameras]);
  // Логические камеры camN — только их закрепляют за моноблоком и контурами.
  const playable = useMemo(() => allPlayable.filter((camera) => isLogicalCamera(camera.src)), [allPlayable]);
  const { ordersById, sessionsByCamera, camerasBySrc, cameraOwners } = useMemo(
    () => ({
      ordersById: indexFirstBy(orders ?? [], (order) => order.id),
      sessionsByCamera: indexFirstBy(sessions ?? [], (session) => session.camera),
      camerasBySrc: indexFirstBy(allPlayable, (camera) => camera.src),
      cameraOwners: cameraOwnersFor(orders, sessions),
    }),
    [allPlayable, orders, sessions],
  );

  useVisiblePolling(reloadOrders, BOARD_POLL_MS);
  useVisiblePolling(reloadSessions, SESSION_POLL_MS);
  // Политика камер — то, что меняет окно «Камеры моноблока»; медленный опрос
  // и «Повторить» перечитывают её вместе с остальным.
  const reloadMonoblockPolicy = async () => {
    await Promise.all([
      reloadCameraSettings(),
      reloadShippingContinuousSettings(),
      reloadShippingContinuousAnalytics(),
      reloadAlwaysOnSettings(),
    ]);
  };
  const reloadSlow = () => Promise.all([reloadCameras(), reloadMonoblockPolicy(), reloadAlwaysOnAnalytics()]);
  useVisiblePolling(reloadSlow, SLOW_POLL_MS);
  const auxiliaryError =
    camerasError ||
    sessionsError ||
    cameraSettingsError ||
    shippingSettingsError ||
    alwaysOnSettingsError ||
    alwaysOnAnalyticsError ||
    shippingContinuousSettingsError ||
    shippingContinuousAnalyticsError;
  const alwaysOnAnalyticsAvailable = !alwaysOnAnalyticsError && alwaysOnAnalytics?.analytics_sync?.available === true;
  const shippingAnalyticsAvailable =
    !shippingContinuousAnalyticsError && shippingContinuousAnalytics?.analytics_sync?.available === true;
  const reloadAll = () => Promise.all([reloadOrders(), reloadSessions(), reloadShippingSettings(), reloadSlow()]);

  /* ── Метрики: считаются на клиенте из уже опрошенных данных ─────────── */
  const transportCounts = useMemo(() => {
    const totals = { truck: 0, train: 0 };
    for (const order of orders ?? []) {
      if (order.status === "shipped" || ORDER_AWAITING_SHIPMENT_STATUSES.includes(order.status)) {
        totals[order.transport_type] += 1;
      }
    }
    for (const session of sessions ?? []) {
      if (!ordersById.has(session.order_id)) totals[session.order_transport_type] += 1;
    }
    return totals;
  }, [orders, ordersById, sessions]);
  const transportTabs: TabDef[] = [
    { key: "truck", label: "Грузовики", count: transportCounts.truck },
    { key: "train", label: "Вагоны", count: transportCounts.train },
  ];
  const boardOrders = useMemo(
    () => orders?.filter((order) => order.transport_type === transportType) ?? [],
    [transportType, orders],
  );
  const boardSessions = useMemo(
    () =>
      (sessions ?? []).filter(
        (session) =>
          (ordersById.get(session.order_id)?.transport_type ?? session.order_transport_type) === transportType,
      ),
    [transportType, ordersById, sessions],
  );
  const counts = useMemo(() => {
    const result = { waiting: 0, loading: 0, ready: 0, shipped: 0, shippedBags: 0 };
    for (const order of boardOrders) {
      if (order.status === "confirmed") {
        result.waiting += 1;
      } else if (order.status === "arrived" || order.status === "loading") {
        result.loading += 1;
      } else if (order.status === "loaded") {
        result.ready += 1;
      } else if (order.status === "shipped") {
        result.shipped += 1;
        result.shippedBags += order.bags_loaded ?? 0;
      }
    }
    return result;
  }, [boardOrders]);
  const completedDays = shippingSettings?.completed_orders_days ?? 1;
  const completedLabel = completedDays <= 1 ? "сегодня" : `за ${completedDays} дн.`;
  // Плитки считаются из тех же строк, что и таблица: под поиском или чужим
  // днём подпись «сегодня» была бы неправдой.
  const boardScopeLabel = debouncedSearch ? "по поиску" : day && day !== today ? formatIsoDate(day) : completedLabel;
  const shippedCaption = counts.shipped > 0 ? `${boardScopeLabel} · ${counts.shippedBags} меш.` : boardScopeLabel;
  /* ── Полоса камер: плитка знает, какой заказ занимает камеру ─────────── */
  const stripSources = shippingContinuousSettings?.camera_sources ?? [];
  function tileBinding(source: string): ShippingTileBinding | undefined {
    const session = sessionsByCamera.get(source);
    const ownerId = session?.order_id ?? cameraOwners[source];
    const order = ownerId != null ? ordersById.get(ownerId) : undefined;
    if (session) {
      return {
        orderId: session.order_id,
        clientName: order?.client_name || session.order_client_name || "Без клиента",
        total: session.last_status?.total ?? 0,
        target: order ? orderedBagCount(order) || null : null,
      };
    }
    if (!order || !["arrived", "loading"].includes(order.status)) return undefined;
    return {
      orderId: order.id,
      clientName: order.client_name || "Без клиента",
      total: order.bags_loaded ?? 0,
      target: orderedBagCount(order) || null,
    };
  }

  /* ── AI 24/7: сводка контура из уже опрошенных настроек ───────────────── */
  const alwaysOnCounts = useMemo(() => {
    const sources = alwaysOnSettings?.camera_sources ?? [];
    const processors = indexFirstBy(alwaysOnSettings?.processors ?? [], (item) => item.cam);
    return {
      total: sources.length,
      running: sources.filter((source) => processors.get(source)?.running).length,
      busy: sources.filter((source) => processors.get(source)?.mode === "session").length,
    };
  }, [alwaysOnSettings]);
  const alwaysOnCamerasCaption =
    alwaysOnCounts.busy > 0
      ? `${alwaysOnCounts.busy} ${pluralRu(alwaysOnCounts.busy, ["занята", "заняты", "заняты"])} отгрузкой`
      : "фоновый подсчёт 24/7";
  const alwaysOnSync =
    alwaysOnSettings?.sync_status !== "synced"
      ? { value: "ожидание", caption: alwaysOnSettings?.detail || "ПК камер ещё не подтвердил контур" }
      : alwaysOnAnalyticsAvailable
        ? { value: "в норме", caption: "ПК камер на связи, журнал синхронизирован" }
        : {
            value: "нет журнала",
            caption:
              alwaysOnAnalyticsError ||
              alwaysOnAnalytics?.analytics_sync?.detail ||
              "журнал событий не синхронизирован",
          };

  const pageTabs: TabDef[] = [
    {
      key: "shipments",
      label: "Отгрузка",
      count: (orders ?? []).filter((order) => ["arrived", "loading"].includes(order.status)).length,
    },
    { key: "monoblock", label: "AI 24/7", count: alwaysOnSettings?.camera_sources.length ?? 0 },
  ];

  return (
    <AppShell title="Моноблок" section="Работа">
      <div className="flex flex-col gap-6">
        <div className="flex flex-wrap items-center gap-3">
          <Tabs tabs={pageTabs} active={tab} onChange={(key) => setTab(key as MonoblockTab)} label="Режим моноблока" />
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {tab === "monoblock" && canManageAlwaysOn && (
              <AlwaysOnSettingsButton cameras={playable} settings={alwaysOnSettings} onSaved={setAlwaysOnSettings} />
            )}
          </div>
        </div>

        {(error || auxiliaryError) && <ErrorAlert message={error || auxiliaryError} onRetry={() => void reloadAll()} />}

        {tab === "monoblock" ? (
          !alwaysOnSettings?.camera_sources.length ? (
            <Card className="rounded-lg px-4 py-12 text-center">
              <div className="text-[14px]">Бесконечный цикл пока не запущен</div>
              <div className="mx-auto mt-1 max-w-md text-[12px] text-[var(--muted-foreground)]">
                {canManageAlwaysOn
                  ? "Выберите камеры в настройке «AI 24/7» — модель начнёт считать круглосуточно; исходный substream будет храниться в техническом архиве 48 часов, а фоновый AI-overlay не публикуется."
                  : "Камеры для постоянного подсчёта пока не настроены. Обратитесь к администратору."}
              </div>
            </Card>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatCard
                  label="Сегодня"
                  value={alwaysOnAnalyticsAvailable ? (alwaysOnAnalytics?.total ?? 0) : "—"}
                  caption={alwaysOnAnalyticsAvailable ? "мешков" : "журнал не синхронизирован"}
                />
                <StatCard
                  label="Всего"
                  value={alwaysOnAnalyticsAvailable ? (alwaysOnAnalytics?.all_time_total ?? 0) : "—"}
                  caption="за всё время"
                />
                <StatCard
                  label="Камер считают"
                  value={`${alwaysOnCounts.running}/${alwaysOnCounts.total}`}
                  caption={alwaysOnCamerasCaption}
                  tone={
                    alwaysOnCounts.total > 0 && alwaysOnCounts.running === alwaysOnCounts.total ? "success" : undefined
                  }
                />
                <StatCard label="Синхронизация" value={alwaysOnSync.value} caption={alwaysOnSync.caption} />
              </div>

              <section className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-x-1.5 text-[12px] text-[var(--muted-foreground)]">
                  <span>Камеры AI 24/7 · считают круглосуточно</span>
                  <span>
                    · {alwaysOnCounts.running} из {alwaysOnCounts.total} в работе
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                  {alwaysOnSettings.camera_sources.map((source) => (
                    <ContinuousCameraTile
                      key={source}
                      scope="ai_247"
                      source={source}
                      settings={alwaysOnSettings}
                      analytics={alwaysOnAnalytics}
                      analyticsError={alwaysOnAnalyticsError}
                      camera={camerasBySrc.get(source)}
                      canManage={canManageAlwaysOn}
                    />
                  ))}
                </div>
              </section>
            </>
          )
        ) : (
          <>
            <Tabs
              tabs={[
                { key: "conveyors", label: "Конвейеры", panelId: `${shippingPanelId}-conveyors` },
                { key: "orders", label: "Заказы", panelId: `${shippingPanelId}-orders` },
              ]}
              active={shippingTab}
              onChange={setShippingTab}
              label="Разделы отгрузки"
            />
            {shippingTab === "conveyors" && (
              <div
                role="tabpanel"
                id={`${shippingPanelId}-conveyors`}
                aria-labelledby={`${shippingPanelId}-conveyors-tab`}
                className="space-y-6"
              >
                <Card role="region" aria-label="Конвейеры и счёт" className="space-y-4 p-4 sm:p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h2 className="text-lg font-semibold">Конвейеры и счёт</h2>
                      <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                        Прямой эфир, счётчики мешков и камеры номеров. Сессии отгрузки по дням — в «Аналитике» камеры.
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <ShippingIdleSettings />
                      {canManage && (
                        <CameraSettingsButton
                          cameras={playable}
                          settings={cameraSettings}
                          reload={reloadMonoblockPolicy}
                        />
                      )}
                    </div>
                  </div>
                  {shippingContinuousSettings ? (
                    <>
                      <div className="flex flex-wrap items-center gap-x-1.5 text-[12px] text-[var(--muted-foreground)]">
                        <span>Камеры отгрузки · работают 24/7</span>
                        <span>
                          · насчитано сегодня{" "}
                          {shippingAnalyticsAvailable ? (shippingContinuousAnalytics?.total ?? 0) : "—"}
                        </span>
                        <span>
                          ·{" "}
                          {shippingContinuousSettings.sync_status !== "synced"
                            ? "ожидает готовности"
                            : shippingAnalyticsAvailable
                              ? "синхронизировано"
                              : "журнал не синхронизирован"}
                        </span>
                      </div>
                      {stripSources.length ? (
                        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
                          {stripSources.map((source) => (
                            <ContinuousCameraTile
                              key={source}
                              scope="shipping"
                              source={source}
                              settings={shippingContinuousSettings}
                              analytics={shippingContinuousAnalytics}
                              analyticsError={shippingContinuousAnalyticsError}
                              camera={camerasBySrc.get(source)}
                              bound={tileBinding(source)}
                            />
                          ))}
                        </div>
                      ) : (
                        <p className="text-[12px] text-[var(--muted-foreground)]">
                          Камеры отгрузки не назначены{canManage ? " — выберите их в «Камеры моноблока»" : ""}
                        </p>
                      )}
                    </>
                  ) : (
                    <p className="text-sm text-[var(--muted-foreground)]">
                      {shippingContinuousSettingsError
                        ? "Данные конвейеров недоступны."
                        : "Данные конвейеров загружаются…"}
                    </p>
                  )}
                </Card>
              </div>
            )}
            {shippingTab === "orders" && (
              <div role="tabpanel" id={`${shippingPanelId}-orders`} aria-labelledby={`${shippingPanelId}-orders-tab`}>
                <Card role="region" aria-label="Заказы отгрузки" className="space-y-4 p-4 sm:p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h2 className="text-lg font-semibold">Календарь отгрузки</h2>
                      <p className="mt-1 text-sm text-[var(--muted-foreground)]">
                        Заказы по дням: что грузить и что уехало. Отгружает грузчик на странице «Грузчик».
                      </p>
                    </div>
                    {canManage && (
                      <Button variant="outline" size="sm" onClick={() => setCompletedOpen(true)}>
                        <CalendarDays className="size-4" /> Отгруженные: {completedLabel}
                      </Button>
                    )}
                  </div>
                  <Tabs
                    tabs={transportTabs}
                    active={transportType}
                    onChange={(key) => setTransportType(key as typeof transportType)}
                    label="Тип транспорта в очереди"
                  />
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <StatCard label="Ожидают погрузки" value={counts.waiting} caption="в очереди" />
                    <StatCard
                      label="На погрузке"
                      value={counts.loading}
                      caption={`${boardSessions.length} с AI-подсчётом`}
                    />
                    <StatCard
                      label="Готовы к выезду"
                      value={counts.ready}
                      caption="ожидают оформления выезда"
                      tone={counts.ready > 0 ? "success" : undefined}
                    />
                    <StatCard label="Выехали" value={counts.shipped} caption={shippedCaption} />
                  </div>

                  <ShippingCalendar
                    orders={orders === null ? null : boardOrders}
                    day={day}
                    today={today}
                    search={search}
                    appliedSearch={debouncedSearch}
                    onDayChange={setDay}
                    onSearchChange={setSearch}
                    canOpenOrder={canOpenOrder}
                  />
                </Card>
              </div>
            )}
          </>
        )}
      </div>

      {canManage && (
        <CompletedOrdersSettingsModal
          open={completedOpen}
          settings={shippingSettings}
          onClose={() => setCompletedOpen(false)}
          // Окно «Выехали» применяет бэкенд — перечитываем очередь сразу,
          // не дожидаясь следующего опроса.
          onSaved={(saved) => {
            setShippingSettings(saved);
            return reloadOrders();
          }}
        />
      )}
    </AppShell>
  );
}

export default function MonoblockPage() {
  // Одно право Моноблока — то же, что бэкенд принимает на GET /orders/?post_board=1.
  return (
    <RequirePerm perm="monoblock.view" title="Моноблок">
      <MonoblockPageInner />
    </RequirePerm>
  );
}
