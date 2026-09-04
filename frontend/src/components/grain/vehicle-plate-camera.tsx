"use client";

import type { AxiosError } from "axios";
import { useState } from "react";
import { Check, Clock3, Focus, PencilLine, Scale, ScanLine, VideoOff, X } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import {
  isDrawableVehicleRoi,
  normalizeVehicleRoi,
  VehicleRoiOverlay,
  type NormalizedRoiPoint,
  type VehicleRoiConfig,
} from "@/components/grain/vehicle-roi-overlay";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { showSuccess } from "@/lib/toast";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const VEHICLE_RUNTIME_BOOTSTRAP_URL = "/cameras/vehicle-plate-runtime/";
const SCALE_AUTOMATION_RUNTIME_URL = "/grain/automatic-passage-scale/runtime/";
const SCALE_AUTOMATION_ACKNOWLEDGE_URL = "/grain/automatic-passage-scale/acknowledge/";
const SCALE_AUTOMATION_SETTINGS_URL = "/grain/automatic-passage-scale/settings/";
const VEHICLE_RUNTIME_POLL_MS = 5_000;
const MIN_STABLE_WEIGHT_SECONDS = 2;
const MAX_STABLE_WEIGHT_SECONDS = 60;
const STABLE_WEIGHT_PRESETS = [5, 10, 15, 20, 30] as const;
const DEFAULT_VEHICLE_ROI: NormalizedRoiPoint[] = [
  [0.25, 0.25],
  [0.75, 0.25],
  [0.9, 0.85],
  [0.1, 0.85],
];

type VehiclePlateMonitor = {
  status: string;
  source: string;
  last_frame_at: string | null;
  last_inference_at: string | null;
  last_confirmed_at: string | null;
  scanned_frames: number;
  plate_detections: number;
  stationary_admissions: number;
  ocr_attempts: number;
  confirmed_events: number;
  durable_duplicates: number;
  consecutive_errors: number;
  inference_avg_ms: number;
  ocr_avg_ms: number;
  has_error: boolean;
  stop_gate: {
    dwell_seconds: number;
    min_frames: number;
    max_movement_ratio: number;
    exit_grace_seconds: number;
  };
};

export type ScaleAutomationRuntime = {
  enabled: boolean;
  /** Optional while old backend instances are draining during a rolling deploy. */
  stable_weight_seconds?: number;
  state:
    | "disabled"
    | "idle"
    | "candidate"
    | "recognizing"
    | "applying"
    | "awaiting_clear"
    | "manual_required"
    | "unavailable";
  last_checked_at: string | null;
  heartbeat_stale: boolean;
  active: {
    request_id: string;
    stage: "claimed" | "recognizing" | "applying" | "done";
    action: "entry" | "exit" | null;
    wagon_id: number | null;
    retryable: boolean;
    error_code: string | null;
  } | null;
};

type ScaleAutomationSettings = {
  stable_weight_seconds: number;
};

export type VehiclePlateRuntime = {
  camera: string;
  enabled: boolean;
  ready: boolean;
  automation_enabled: boolean;
  camera_configured: boolean;
  weight_first_enabled: boolean;
  on_demand_enabled: boolean;
  on_demand_camera_configured: boolean;
  source: "main" | "sub";
  stream: string;
  server_push_configured: boolean;
  diagnostic: string;
  monitor: VehiclePlateMonitor | null;
  roi: VehicleRoiConfig;
  /** Optional during a rolling deploy; absence must never be shown as healthy. */
  scale_automation?: ScaleAutomationRuntime;
};

type ScaleAutomationAcknowledgeResponse = {
  acknowledged: true;
  scale_automation: ScaleAutomationRuntime;
};

type VehicleRoiSaveResponse = {
  saved: true;
  applied_to_monitor: boolean;
  roi: VehicleRoiConfig;
};

type SaveNotice = { message: string; tone: "success" | "warning" };

type RuntimePresentation = {
  label: string;
  detail: string;
  tone: "good" | "warn" | "bad" | "idle";
};

const TONE_CLASSES: Record<RuntimePresentation["tone"], string> = {
  good: "border-emerald-400/25 bg-emerald-400/10 text-emerald-300",
  warn: "border-amber-400/25 bg-amber-400/10 text-amber-200",
  bad: "border-rose-400/25 bg-rose-400/10 text-rose-200",
  idle: "border-white/15 bg-white/[0.06] text-white/60",
};

function validStableWeightSeconds(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= MIN_STABLE_WEIGHT_SECONDS &&
    value <= MAX_STABLE_WEIGHT_SECONDS
  );
}

function secondsLabel(seconds: number): string {
  const mod100 = seconds % 100;
  if (mod100 >= 11 && mod100 <= 14) return `${seconds} секунд`;
  const mod10 = seconds % 10;
  if (mod10 === 1) return `${seconds} секунду`;
  if (mod10 >= 2 && mod10 <= 4) return `${seconds} секунды`;
  return `${seconds} секунд`;
}

function draftRoi(points: NormalizedRoiPoint[], source: "main" | "sub"): VehicleRoiConfig {
  return {
    configured: true,
    enabled: true,
    source,
    coordinate_space: "normalized",
    points: points.map(([x, y]) => ({ x, y })),
  };
}

function polygonArea(points: NormalizedRoiPoint[]) {
  let doubledArea = 0;
  for (let index = 0; index < points.length; index += 1) {
    const current = points[index];
    const next = points[(index + 1) % points.length];
    doubledArea += current[0] * next[1] - next[0] * current[1];
  }
  return Math.abs(doubledArea) / 2;
}

function validRoiDraft(points: NormalizedRoiPoint[]) {
  return points.length >= 3 && points.length <= 12 && polygonArea(points) >= 0.0001;
}

function acceptedSaveResponse(value: unknown, expectedSource: "main" | "sub"): value is VehicleRoiSaveResponse {
  if (!value || typeof value !== "object") return false;
  const payload = value as Partial<VehicleRoiSaveResponse>;
  const points = normalizeVehicleRoi(payload.roi?.points);
  return (
    payload.saved === true &&
    typeof payload.applied_to_monitor === "boolean" &&
    isDrawableVehicleRoi(payload.roi, expectedSource) &&
    validRoiDraft(points)
  );
}

export function vehicleAiPresentation(
  runtime: VehiclePlateRuntime | null,
  loading: boolean,
  error: string,
): RuntimePresentation {
  if (error) {
    return { label: "AI: НЕТ СВЯЗИ", detail: "CRM не получила свежий статус модели.", tone: "bad" };
  }
  if (!runtime) {
    return loading
      ? { label: "AI: ПРОВЕРКА", detail: "Запрашиваем состояние модели.", tone: "idle" }
      : { label: "AI: НЕТ ДАННЫХ", detail: "Статус модели не получен.", tone: "bad" };
  }
  if (!runtime.enabled) {
    return { label: "AI: ОТКЛЮЧЕНА", detail: "Модель номеров выключена на ПК камер.", tone: "bad" };
  }
  if (!runtime.ready) {
    return { label: "AI: НЕ ГОТОВА", detail: "Детектор номера или OCR не загрузился.", tone: "bad" };
  }
  if (runtime.weight_first_enabled || runtime.scale_automation?.enabled) {
    if (!runtime.on_demand_enabled) {
      return {
        label: "AI ПО ЗАПРОСУ ВЫКЛ.",
        detail: "ПК камер ещё не включён в режим распознавания после стабильного веса.",
        tone: "bad",
      };
    }
    if (!runtime.on_demand_camera_configured) {
      return {
        label: "КАМЕРА НЕ НАЗНАЧЕНА",
        detail: `${runtime.camera} не добавлена в распознавание после веса.`,
        tone: "bad",
      };
    }
    if (!isDrawableVehicleRoi(runtime.roi, runtime.source)) {
      return { label: "ROI НЕ ГОТОВ", detail: "Сначала сохраните рабочую зону номера.", tone: "warn" };
    }
    return {
      label: "AI ГОТОВА",
      detail: "После стабильного веса камера получит свежие кадры и подтвердит номер OCR.",
      tone: "good",
    };
  }
  if (!runtime.automation_enabled) {
    return { label: "АВТОМАТИКА ВЫКЛ.", detail: "Автоматический запуск модели выключен.", tone: "warn" };
  }
  if (!runtime.camera_configured) {
    return {
      label: "КАМЕРА НЕ НАЗНАЧЕНА",
      detail: `${runtime.camera} не добавлена в автоматическое распознавание.`,
      tone: "warn",
    };
  }
  if (!isDrawableVehicleRoi(runtime.roi, runtime.source)) {
    return { label: "ROI НЕ ГОТОВ", detail: "Зона остановки отсутствует, выключена или повреждена.", tone: "warn" };
  }
  if (!runtime.monitor) {
    return { label: "AI: НЕ ЗАПУЩЕНА", detail: "Монитор камеры не создан на ПК камер.", tone: "bad" };
  }
  if (runtime.monitor.source !== runtime.source) {
    return {
      label: "ПОТОК МОНИТОРА НЕ СОВПАЛ",
      detail: "Монитор запущен для другого потока камеры; статус отклонён как недостоверный.",
      tone: "bad",
    };
  }
  if (runtime.monitor.status === "model_unavailable") {
    return { label: "AI: НЕТ МОДЕЛИ", detail: "Монитор запущен без готового детектора или OCR.", tone: "bad" };
  }
  if (runtime.monitor.status === "reconnecting") {
    return {
      label: "КАМЕРА: ПЕРЕПОДКЛЮЧЕНИЕ",
      detail: "AI потерял поток камеры и пытается подключиться заново.",
      tone: "bad",
    };
  }
  if (runtime.monitor.status === "stopped") {
    return { label: "AI: МОНИТОР ОСТАНОВЛЕН", detail: "Обработка этой камеры остановлена.", tone: "bad" };
  }
  if (runtime.monitor.status === "roi_source_mismatch") {
    return { label: "ПОТОК ROI НЕ СОВПАЛ", detail: "ROI сохранён для другого потока камеры.", tone: "bad" };
  }
  if (runtime.monitor.status === "roi_missing_or_disabled" || runtime.monitor.status === "roi_error") {
    return { label: "ROI НЕ ГОТОВ", detail: "Монитор не может использовать зону остановки.", tone: "warn" };
  }
  if (runtime.monitor.status === "warming") {
    return { label: "AI ПРОГРЕВАЕТСЯ", detail: "Поток подключён, ожидаем первый обработанный кадр.", tone: "warn" };
  }
  if (runtime.monitor.status === "degraded" || runtime.monitor.has_error) {
    return { label: "ОШИБКА AI", detail: "Последние циклы распознавания завершились ошибкой.", tone: "bad" };
  }
  if (runtime.monitor.status === "online") {
    if (!runtime.server_push_configured) {
      return {
        label: "ОТПРАВКА НЕ НАСТРОЕНА",
        detail: "AI может прочитать номер, но не может передать событие в CRM.",
        tone: "warn",
      };
    }
    return {
      label: "AI РАБОТАЕТ",
      detail: `Кадров: ${runtime.monitor.scanned_frames} · найдено номеров: ${runtime.monitor.plate_detections}`,
      tone: "good",
    };
  }
  return { label: "AI ЗАПУСКАЕТСЯ", detail: "ROI загружен, ожидаем первый обработанный кадр.", tone: "warn" };
}

export function scaleAutomationPresentation(
  automation: ScaleAutomationRuntime | null | undefined,
  loading: boolean,
  error: string,
): RuntimePresentation {
  if (error) {
    return {
      label: "АВТОМАТИКА: НЕТ СВЯЗИ",
      detail: "Не удалось проверить фоновый контроль весов. Используйте ручное оформление.",
      tone: "bad",
    };
  }
  if (!automation) {
    return loading
      ? { label: "АВТОМАТИКА: ПРОВЕРКА", detail: "Запрашиваем состояние контроля весов.", tone: "idle" }
      : {
          label: "СТАТУС АВТОМАТИКИ НЕДОСТУПЕН",
          detail: "CRM не получила состояние фонового контроля весов. Ручное оформление остаётся доступным.",
          tone: "bad",
        };
  }
  if (automation.state === "manual_required") {
    const wagon = automation.active?.wagon_id;
    return {
      label: "НУЖЕН ОПЕРАТОР",
      detail: wagon
        ? `Автоматика остановила рейс #${wagon}; завершите его ручными кнопками.`
        : "Автоматика не смогла продолжить этот заезд; оформите вывоз вручную.",
      tone: "bad",
    };
  }
  if (automation.heartbeat_stale) {
    return {
      label: "АВТОМАТИКА НЕ ОТВЕЧАЕТ",
      detail: "Фоновый обработчик давно не отмечался. Используйте ручное оформление.",
      tone: "bad",
    };
  }
  if (!automation.enabled || automation.state === "disabled") {
    return {
      label: "РУЧНОЙ РЕЖИМ",
      detail: "Фоновый контроль весов выключен; рейс можно оформить и взвесить вручную.",
      tone: "idle",
    };
  }
  if (automation.state === "idle") {
    return {
      label: "ОЖИДАЕТ МАШИНУ",
      detail: validStableWeightSeconds(automation.stable_weight_seconds)
        ? `Фоновый сервис проверяет весы каждую секунду. OCR запустится, когда вес останется стабильным ${secondsLabel(automation.stable_weight_seconds)}.`
        : "Фоновый сервис проверяет весы каждую секунду и ждёт новый стабильный заезд.",
      tone: "good",
    };
  }
  if (automation.state === "candidate") {
    return {
      label: "ЖДЁТ СТАБИЛЬНЫЙ ВЕС",
      detail: validStableWeightSeconds(automation.stable_weight_seconds)
        ? `Обнаружено изменение веса; перед запуском камеры оно должно быть стабильным ${secondsLabel(automation.stable_weight_seconds)}.`
        : "Обнаружено изменение веса; ждём стабильное показание перед запуском камеры.",
      tone: "warn",
    };
  }
  if (automation.state === "recognizing") {
    return {
      label: "РАСПОЗНАЁТ НОМЕР",
      detail: "Камера обрабатывает новый стабильный заезд и подтверждает номер машины.",
      tone: "warn",
    };
  }
  if (automation.state === "applying") {
    return {
      label: "ОБНОВЛЯЕТ РЕЙС",
      detail: "Номер получен; вес и следующий статус рейса сохраняются.",
      tone: "warn",
    };
  }
  if (automation.state === "awaiting_clear") {
    return {
      label: "ЖДЁТ ОСВОБОЖДЕНИЯ ВЕСОВ",
      detail: "Вес уже обработан. Новый цикл начнётся только после того, как машина съедет с весов.",
      tone: "good",
    };
  }
  return {
    label: "ВЕСЫ НЕДОСТУПНЫ",
    detail: "Весы или фоновый обработчик недоступны. Используйте ручное оформление.",
    tone: "bad",
  };
}

function vehiclePipelineHint(monitor: VehiclePlateMonitor, source: "main" | "sub"): string {
  if (monitor.scanned_frames === 0) return `С запуска монитор ещё не получил ни одного кадра ${source}.`;
  if (monitor.plate_detections === 0) return "С запуска кадры поступали, но детектор не увидел номерной знак.";
  if (monitor.stationary_admissions === 0) {
    return "С запуска номер находился, но его центр не прошёл ROI и фильтр остановки.";
  }
  if (monitor.ocr_attempts === 0) return "С запуска остановка допускалась, но OCR не обработал номер.";
  if (monitor.confirmed_events === 0) return "OCR запускался, но ещё нет трёх совпадающих чтений номера.";
  return "Счётчики накопительные: во время остановки смотрите, какой этап перестал расти.";
}

function roiPresentation(runtime: VehiclePlateRuntime | null, error: string): RuntimePresentation {
  if (error) return { label: "ROI НЕДОСТУПЕН", detail: "Не удалось проверить актуальную зону.", tone: "bad" };
  const roi = runtime?.roi;
  if (!roi?.configured) return { label: "ROI НЕ ЗАДАН", detail: "Зона остановки ещё не настроена.", tone: "warn" };
  if (!roi.enabled) return { label: "ROI ВЫКЛЮЧЕН", detail: "Сохранённая зона сейчас отключена.", tone: "warn" };
  if (!runtime || roi.source !== runtime.source) {
    return {
      label: "ROI ДЛЯ ДРУГОГО ПОТОКА",
      detail: `Зона сохранена для ${roi.source || "другого потока"}.`,
      tone: "bad",
    };
  }
  if (!isDrawableVehicleRoi(roi, runtime.source)) {
    return { label: "ROI ПОВРЕЖДЁН", detail: "Координаты зоны нельзя безопасно показать.", tone: "bad" };
  }
  return { label: "ROI ПОКАЗАН", detail: "Голубая область — зона контроля остановки.", tone: "good" };
}

/**
 * Live view of the backend-configured vehicle-plate lane used by grain export.
 * CameraStream keeps the go2rtc signalling behind the authenticated endpoint;
 * this component only selects the logical stream name.
 */
export function VehiclePlateCameraWorkspace() {
  const isSuperuser = useAuth((state) => Boolean(state.me?.is_superuser));
  const canManageRoi = isSuperuser;
  const canAcknowledgeManualPassage = useAuth((state) => can(state.me, "grain.weigh"));
  const [streamOnline, setStreamOnline] = useState(false);
  const [editingRoi, setEditingRoi] = useState(false);
  const [roiDraft, setRoiDraft] = useState<NormalizedRoiPoint[]>([]);
  const [savingRoi, setSavingRoi] = useState(false);
  const [roiSaveError, setRoiSaveError] = useState("");
  const [saveNotice, setSaveNotice] = useState<SaveNotice | null>(null);
  const [acknowledgingRequestId, setAcknowledgingRequestId] = useState("");
  const [acknowledgeError, setAcknowledgeError] = useState<{ requestId: string; message: string } | null>(null);
  const [stableWeightSettingsOpen, setStableWeightSettingsOpen] = useState(false);
  const [stableWeightSecondsDraft, setStableWeightSecondsDraft] = useState("");
  const [savingStableWeightSeconds, setSavingStableWeightSeconds] = useState(false);
  const [stableWeightSettingsSaveError, setStableWeightSettingsSaveError] = useState("");
  const {
    data: runtime,
    loading: runtimeLoading,
    error: runtimeError,
    reload,
    setData: setRuntime,
  } = useApi<VehiclePlateRuntime>(VEHICLE_RUNTIME_BOOTSTRAP_URL);
  const {
    data: standaloneScaleAutomation,
    loading: scaleAutomationLoading,
    error: scaleAutomationError,
    reload: reloadScaleAutomation,
    setData: setStandaloneScaleAutomation,
  } = useApi<ScaleAutomationRuntime>(SCALE_AUTOMATION_RUNTIME_URL);
  const {
    data: scaleAutomationSettings,
    loading: scaleAutomationSettingsLoading,
    error: scaleAutomationSettingsError,
    reload: reloadScaleAutomationSettings,
    setData: setScaleAutomationSettings,
  } = useApi<ScaleAutomationSettings>(SCALE_AUTOMATION_SETTINGS_URL);
  useVisiblePolling(
    reload,
    VEHICLE_RUNTIME_POLL_MS,
    !runtimeLoading && !editingRoi && !savingRoi && !acknowledgingRequestId,
  );
  useVisiblePolling(reloadScaleAutomation, VEHICLE_RUNTIME_POLL_MS, !scaleAutomationLoading && !acknowledgingRequestId);
  useVisiblePolling(
    reloadScaleAutomationSettings,
    VEHICLE_RUNTIME_POLL_MS,
    !scaleAutomationSettingsLoading && !savingStableWeightSeconds,
  );
  const aiState = vehicleAiPresentation(runtime, runtimeLoading, runtimeError);
  // The CRM endpoint stays available when Camera-PC diagnostics fail. Keep the
  // embedded field only as a rolling-deploy fallback for an older backend.
  const embeddedScaleAutomation = runtime?.scale_automation;
  const healthyEmbeddedScaleAutomation = runtimeError ? undefined : embeddedScaleAutomation;
  const scaleAutomation =
    standaloneScaleAutomation && !scaleAutomationError
      ? standaloneScaleAutomation
      : (healthyEmbeddedScaleAutomation ?? standaloneScaleAutomation ?? embeddedScaleAutomation);
  const usingHealthyEmbeddedFallback = !standaloneScaleAutomation && Boolean(embeddedScaleAutomation) && !runtimeError;
  const effectiveScaleAutomationError = usingHealthyEmbeddedFallback
    ? ""
    : scaleAutomationError || (!standaloneScaleAutomation ? runtimeError : "");
  const weightFirst = Boolean(
    runtime?.weight_first_enabled || scaleAutomation?.enabled || scaleAutomation?.state === "manual_required",
  );
  const stableWeightSeconds = validStableWeightSeconds(scaleAutomation?.stable_weight_seconds)
    ? scaleAutomation.stable_weight_seconds
    : validStableWeightSeconds(scaleAutomationSettings?.stable_weight_seconds)
      ? scaleAutomationSettings.stable_weight_seconds
      : null;
  const canOpenStableWeightSettings =
    isSuperuser &&
    weightFirst &&
    !scaleAutomationSettingsLoading &&
    !scaleAutomationSettingsError &&
    validStableWeightSeconds(scaleAutomationSettings?.stable_weight_seconds);
  const stableWeightSecondsValue = Number(stableWeightSecondsDraft);
  const canSaveStableWeightSeconds =
    stableWeightSecondsDraft.trim() !== "" &&
    validStableWeightSeconds(stableWeightSecondsValue) &&
    !savingStableWeightSeconds;
  const scaleAutomationWithSettings =
    scaleAutomation && stableWeightSeconds !== null
      ? { ...scaleAutomation, stable_weight_seconds: stableWeightSeconds }
      : scaleAutomation;
  const scaleAutomationState = scaleAutomationPresentation(
    scaleAutomationWithSettings,
    !scaleAutomation && scaleAutomationLoading,
    effectiveScaleAutomationError,
  );
  const manualRequiredRequestId =
    scaleAutomation?.state === "manual_required" ? (scaleAutomation.active?.request_id ?? "") : "";
  const acknowledgingManualPassage = Boolean(acknowledgingRequestId);
  const currentAcknowledgeError =
    acknowledgeError?.requestId === manualRequiredRequestId ? acknowledgeError.message : "";
  const roiState = editingRoi
    ? { label: "ROI РЕДАКТИРУЕТСЯ", detail: "Перетащите точки и сохраните новую зону.", tone: "warn" as const }
    : roiPresentation(runtime, runtimeError);
  const overlayRoi = editingRoi && runtime ? draftRoi(roiDraft, runtime.source) : runtimeError ? null : runtime?.roi;
  const canSaveRoi = validRoiDraft(roiDraft) && !savingRoi;

  function startRoiEditor() {
    if (!canManageRoi || !runtime || runtimeLoading || runtimeError) return;
    const serverPoints = normalizeVehicleRoi(runtime.roi.points);
    setRoiDraft(serverPoints.length ? serverPoints : DEFAULT_VEHICLE_ROI);
    setRoiSaveError("");
    setSaveNotice(null);
    setEditingRoi(true);
  }

  function cancelRoiEditor() {
    setEditingRoi(false);
    setRoiDraft([]);
    setRoiSaveError("");
  }

  function acceptSavedRoi(payload: VehicleRoiSaveResponse) {
    if (!runtime) return;
    setRuntime({ ...runtime, roi: payload.roi });
    setEditingRoi(false);
    setRoiDraft([]);
    setRoiSaveError("");
    setSaveNotice(
      weightFirst
        ? {
            message: "ROI сохранён. Следующее распознавание после стабильного веса использует новую зону.",
            tone: "success",
          }
        : payload.applied_to_monitor
          ? {
              message:
                "ROI сохранён. ПК камер получил запрос на обновление; новая зона обычно применяется в течение 2 секунд.",
              tone: "success",
            }
          : {
              message:
                "ROI сохранён, но монитор пока не подтвердил обновление. Он перечитает зону после восстановления.",
              tone: "warning",
            },
    );
    if (weightFirst || payload.applied_to_monitor) showSuccess("ROI камеры сохранён");
  }

  async function saveRoi() {
    if (!canManageRoi || !runtime || !canSaveRoi) return;
    setSavingRoi(true);
    setRoiSaveError("");
    const body = {
      points: roiDraft.map(([x, y]) => ({ x, y })),
      enabled: true,
      source: runtime.source,
    };
    const runtimeUrl = `/cameras/${runtime.camera}/vehicle-plate-runtime/`;
    try {
      const response = await api.put<VehicleRoiSaveResponse>(runtimeUrl, body, { timeout: 12_000 });
      if (!acceptedSaveResponse(response.data, runtime.source)) throw new Error("invalid vehicle ROI response");
      acceptSavedRoi(response.data);
    } catch (cause) {
      // A 503 may mean that the polygon was persisted while the live monitor
      // refresh failed. Keep that authoritative value instead of rolling back.
      const errorResponse = (cause as AxiosError<unknown>).response;
      const partial = errorResponse?.data;
      if (errorResponse?.status === 503 && acceptedSaveResponse(partial, runtime.source)) acceptSavedRoi(partial);
      else setRoiSaveError(apiError(cause));
    } finally {
      setSavingRoi(false);
    }
  }

  async function acknowledgeManualPassage() {
    const requestId = manualRequiredRequestId;
    if (!canAcknowledgeManualPassage || !requestId || acknowledgingRequestId) return;
    setAcknowledgingRequestId(requestId);
    setAcknowledgeError(null);
    try {
      const response = await api.post<ScaleAutomationAcknowledgeResponse>(SCALE_AUTOMATION_ACKNOWLEDGE_URL, {
        request_id: requestId,
        resolved: true,
      });
      setStandaloneScaleAutomation(response.data.scale_automation);
      showSuccess("Ручная обработка подтверждена");
      await reloadScaleAutomation().catch(() => undefined);
    } catch {
      // Keep backend diagnostics (which may contain recognition details) out of
      // the grain.view runtime UI.
      setAcknowledgeError({
        requestId,
        message: "Не удалось подтвердить ручную обработку. Повторите попытку.",
      });
    } finally {
      setAcknowledgingRequestId("");
    }
  }

  function openStableWeightSettings() {
    if (!canOpenStableWeightSettings || stableWeightSeconds === null) return;
    setStableWeightSecondsDraft(String(stableWeightSeconds));
    setStableWeightSettingsSaveError("");
    setStableWeightSettingsOpen(true);
  }

  function closeStableWeightSettings() {
    if (savingStableWeightSeconds) return;
    setStableWeightSettingsOpen(false);
    setStableWeightSettingsSaveError("");
  }

  async function saveStableWeightSettings() {
    if (!isSuperuser || !canSaveStableWeightSeconds) return;
    setSavingStableWeightSeconds(true);
    setStableWeightSettingsSaveError("");
    try {
      const response = await api.patch<ScaleAutomationSettings>(SCALE_AUTOMATION_SETTINGS_URL, {
        stable_weight_seconds: stableWeightSecondsValue,
      });
      if (!validStableWeightSeconds(response.data?.stable_weight_seconds)) {
        throw new Error("invalid automatic passage scale settings response");
      }
      setScaleAutomationSettings(response.data);
      if (standaloneScaleAutomation) {
        setStandaloneScaleAutomation({
          ...standaloneScaleAutomation,
          stable_weight_seconds: response.data.stable_weight_seconds,
        });
      }
      setStableWeightSettingsOpen(false);
      showSuccess("Время ожидания стабильного веса сохранено");
      await Promise.all([reloadScaleAutomation(), reloadScaleAutomationSettings()]).catch(() => undefined);
    } catch (cause) {
      setStableWeightSettingsSaveError(apiError(cause));
    } finally {
      setSavingStableWeightSeconds(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-sky-700">
            {weightFirst ? "Автоматический вывоз по весам" : "Камера автоматического вывоза"}
          </p>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            {weightFirst
              ? stableWeightSeconds
                ? `Сервис проверяет весы каждую секунду. Вес должен оставаться стабильным ${secondsLabel(stableWeightSeconds)}, затем запускается OCR. Повторный запуск возможен только после освобождения весов.`
                : "Сервис проверяет весы каждую секунду. OCR запускается после настроенного времени стабильного веса; повторный запуск возможен только после освобождения весов."
              : "Камера проходной показывает машину, номер которой используется для автоматического рейса."}
          </p>
        </div>
        {canManageRoi || (isSuperuser && weightFirst) ? (
          <div className="flex flex-wrap items-center gap-2">
            {isSuperuser && weightFirst && !editingRoi ? (
              <Button
                variant="outline"
                disabled={!canOpenStableWeightSettings}
                onClick={openStableWeightSettings}
                title={scaleAutomationSettingsError ? "Настройка ожидания временно недоступна" : undefined}
              >
                <Clock3 className="size-4" /> Настроить ожидание
              </Button>
            ) : null}
            {canManageRoi && editingRoi ? (
              <div className="flex flex-wrap items-center gap-2" aria-label="Действия редактора ROI">
                <Button variant="outline" disabled={savingRoi} onClick={cancelRoiEditor}>
                  <X className="size-4" /> Отмена
                </Button>
                <Button disabled={!canSaveRoi} onClick={() => void saveRoi()}>
                  <Check className="size-4" /> {savingRoi ? "Сохранение…" : "Сохранить ROI"}
                </Button>
              </div>
            ) : canManageRoi ? (
              <Button
                variant="outline"
                disabled={!runtime || runtimeLoading || Boolean(runtimeError)}
                onClick={startRoiEditor}
              >
                <PencilLine className="size-4" /> Изменить ROI
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      {editingRoi ? (
        <p role="status" className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900">
          Перетащите голубые точки мышью или выберите точку клавишей Tab и двигайте стрелками. Shift + стрелка — крупный
          шаг.
        </p>
      ) : null}
      {roiSaveError ? (
        <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
          {roiSaveError}
        </p>
      ) : null}
      {saveNotice ? (
        <p
          role="status"
          className={cn(
            "rounded-xl border px-4 py-3 text-sm",
            saveNotice.tone === "success"
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-amber-200 bg-amber-50 text-amber-900",
          )}
        >
          {saveNotice.message}
        </p>
      ) : null}

      <section
        aria-label="Камера проходной на вывоз"
        className="overflow-hidden rounded-[28px] border border-slate-800 bg-[#111318] text-white shadow-[0_24px_60px_rgba(15,23,42,0.18)]"
      >
        <div className="grid lg:aspect-[44/16] lg:grid-cols-[1.55fr_0.85fr]">
          <div className="relative aspect-video min-h-72 overflow-hidden bg-black lg:aspect-auto lg:min-h-0">
            {runtime?.stream ? (
              <CameraStream
                key={runtime.stream}
                src={runtime.stream}
                onStateChange={setStreamOnline}
                className="absolute inset-0 size-full object-cover"
              />
            ) : null}
            <VehicleRoiOverlay
              roi={overlayRoi}
              expectedSource={runtime?.source ?? "main"}
              editable={editingRoi}
              onPointsChange={setRoiDraft}
            />
            {!streamOnline && (
              <div className="absolute inset-0 z-[2] flex flex-col items-center justify-center gap-2 bg-black text-white/40">
                <VideoOff className="size-7" />
                <span className="text-xs">Ожидаем видеопоток</span>
              </div>
            )}

            <div className="absolute inset-x-0 top-0 z-[3] flex items-center justify-between bg-gradient-to-b from-black/80 to-transparent p-4 pb-12">
              <span className="flex items-center gap-2 rounded-full border border-sky-300/25 bg-black/45 px-3 py-1.5 text-[10px] font-bold tracking-[0.14em] text-sky-200 backdrop-blur-md">
                <span className={cn("size-1.5 rounded-full", streamOnline ? "bg-emerald-400" : "bg-amber-400")} />
                ПРОХОДНАЯ · ВЫВОЗ
              </span>
              <span className="rounded-full bg-black/45 px-3 py-1.5 text-[10px] font-semibold text-white/65 backdrop-blur-md">
                Камера {runtime?.camera ?? "—"} · поток/OCR: {runtime?.source ?? "—"}
              </span>
            </div>

            <div className="absolute inset-x-0 bottom-0 z-[3] bg-gradient-to-t from-black/90 to-transparent p-4 pt-14">
              <p className="text-lg font-bold">Камера проходной</p>
              <p className="mt-0.5 text-xs text-white/50">Распознавание номера машины</p>
            </div>
          </div>

          <div className="flex flex-col p-6 lg:min-h-0 lg:overflow-y-auto">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-sky-300">Прямой эфир</p>
                <h2 className="mt-2 text-2xl font-bold tracking-tight">
                  {weightFirst ? "Вес → номер → статус" : "Автоматический вывоз"}
                </h2>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-1.5">
                <span
                  className={cn(
                    "rounded-full border px-3 py-1 text-[10px] font-bold",
                    streamOnline
                      ? "border-emerald-400/25 bg-emerald-400/10 text-emerald-300"
                      : "border-amber-400/25 bg-amber-400/10 text-amber-200",
                  )}
                >
                  ВИДЕО: {streamOnline ? "В ЭФИРЕ" : "НЕТ СИГНАЛА"}
                </span>
                <span className={cn("rounded-full border px-3 py-1 text-[10px] font-bold", TONE_CLASSES[aiState.tone])}>
                  {aiState.label}
                </span>
              </div>
            </div>

            <div className="mt-7 grid gap-3">
              <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                <span
                  className={cn(
                    "flex size-10 shrink-0 items-center justify-center rounded-xl",
                    aiState.tone === "good" ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-300",
                  )}
                >
                  <ScanLine className="size-5" />
                </span>
                <div className="min-w-0">
                  <p className="text-[11px] text-white/40">Состояние модели</p>
                  <p className="mt-0.5 text-sm font-bold">{aiState.label}</p>
                  <p className="mt-1 text-[11px] leading-4 text-white/45">{aiState.detail}</p>
                </div>
              </div>
              <div className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4">
                <span
                  className={cn(
                    "flex size-10 shrink-0 items-center justify-center rounded-xl",
                    roiState.tone === "good" ? "bg-sky-400/10 text-sky-300" : "bg-amber-400/10 text-amber-300",
                  )}
                >
                  <Focus className="size-5" />
                </span>
                <div className="min-w-0">
                  <p className="text-[11px] text-white/40">Зона остановки</p>
                  <p className="mt-0.5 text-sm font-bold">{roiState.label}</p>
                  <p className="mt-1 text-[11px] leading-4 text-white/45">{roiState.detail}</p>
                </div>
              </div>
              {weightFirst ? (
                <div
                  role="region"
                  aria-label="Автоматика весов"
                  className="flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.04] p-4"
                >
                  <span
                    className={cn(
                      "flex size-10 shrink-0 items-center justify-center rounded-xl border",
                      TONE_CLASSES[scaleAutomationState.tone],
                    )}
                  >
                    <Scale className="size-5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="text-[11px] text-white/40">Автоматика весов</p>
                    <p className="mt-0.5 text-sm font-bold">{scaleAutomationState.label}</p>
                    <p className="mt-1 text-[11px] leading-4 text-white/45">{scaleAutomationState.detail}</p>
                    {canAcknowledgeManualPassage && manualRequiredRequestId ? (
                      <div className="mt-3">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={acknowledgingManualPassage}
                          onClick={() => void acknowledgeManualPassage()}
                          className="border-white/20 bg-white/[0.06] text-white hover:bg-white/10 hover:text-white"
                        >
                          {acknowledgingManualPassage ? "Подтверждение…" : "Подтвердить ручную обработку"}
                        </Button>
                        {currentAcknowledgeError ? (
                          <p role="alert" className="mt-2 text-[11px] leading-4 text-rose-200">
                            {currentAcknowledgeError}
                          </p>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : null}
              {runtime?.monitor && !weightFirst ? (
                <div
                  role="region"
                  aria-label="Этапы распознавания номера"
                  className="rounded-2xl border border-white/10 bg-white/[0.04] p-4"
                >
                  <p className="text-[11px] text-white/40">Диагностика по этапам · с запуска</p>
                  <div className="mt-3 grid grid-cols-5 gap-1 text-center">
                    {[
                      ["Кадры", runtime.monitor.scanned_frames],
                      ["Номера", runtime.monitor.plate_detections],
                      ["Стоп", runtime.monitor.stationary_admissions],
                      ["OCR", runtime.monitor.ocr_attempts],
                      ["Готово", runtime.monitor.confirmed_events],
                    ].map(([label, value]) => (
                      <div key={label} className="min-w-0 rounded-lg bg-black/20 px-1 py-2">
                        <p className="truncate text-[9px] uppercase tracking-wide text-white/35">{label}</p>
                        <p className="mt-0.5 text-sm font-bold tabular-nums text-white/85">{value}</p>
                      </div>
                    ))}
                  </div>
                  <p className="mt-3 text-[11px] leading-4 text-white/50">
                    {vehiclePipelineHint(runtime.monitor, runtime.source)}
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </section>

      <Modal
        open={stableWeightSettingsOpen}
        onClose={closeStableWeightSettings}
        eyebrow="Настройка суперпользователя"
        title="Ожидание стабильного веса"
        description="Укажите, сколько секунд вес должен оставаться стабильным перед запуском распознавания номера."
        footer={
          <>
            <Button variant="ghost" disabled={savingStableWeightSeconds} onClick={closeStableWeightSettings}>
              Отмена
            </Button>
            <Button disabled={!canSaveStableWeightSeconds} onClick={() => void saveStableWeightSettings()}>
              <Check className="size-4" /> {savingStableWeightSeconds ? "Сохранение…" : "Сохранить"}
            </Button>
          </>
        }
      >
        <div className="rounded-2xl border bg-slate-50 p-4">
          <label htmlFor="stable-weight-seconds" className="block text-sm font-bold text-slate-800">
            Время стабильного веса
          </label>
          <p id="stable-weight-seconds-hint" className="mt-1 text-xs text-slate-500">
            От {MIN_STABLE_WEIGHT_SECONDS} до {MAX_STABLE_WEIGHT_SECONDS} секунд
          </p>
          <div className="relative mt-3 max-w-40">
            <Input
              id="stable-weight-seconds"
              type="number"
              min={MIN_STABLE_WEIGHT_SECONDS}
              max={MAX_STABLE_WEIGHT_SECONDS}
              step={1}
              inputMode="numeric"
              value={stableWeightSecondsDraft}
              aria-describedby="stable-weight-seconds-hint"
              disabled={savingStableWeightSeconds}
              onChange={(event) => setStableWeightSecondsDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && canSaveStableWeightSeconds) {
                  event.preventDefault();
                  void saveStableWeightSettings();
                }
              }}
              className="pr-10 text-center text-lg font-bold tabular-nums"
              autoFocus
            />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400">
              сек.
            </span>
          </div>
          <div className="mt-4 grid grid-cols-5 gap-2" aria-label="Готовые варианты ожидания">
            {STABLE_WEIGHT_PRESETS.map((seconds) => (
              <button
                type="button"
                key={seconds}
                disabled={savingStableWeightSeconds}
                onClick={() => setStableWeightSecondsDraft(String(seconds))}
                className={cn(
                  "rounded-lg border py-2 text-xs font-bold transition-colors disabled:opacity-60",
                  stableWeightSecondsValue === seconds
                    ? "border-blue-600 bg-blue-600 text-white"
                    : "bg-white text-slate-600 hover:border-blue-300",
                )}
              >
                {seconds}
              </button>
            ))}
          </div>
        </div>
        {stableWeightSettingsSaveError ? (
          <p role="alert" className="mt-3 text-sm text-[var(--destructive)]">
            {stableWeightSettingsSaveError}
          </p>
        ) : null}
      </Modal>
    </div>
  );
}
