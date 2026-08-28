"use client";

import { useState } from "react";
import { Focus, ScanLine, VideoOff } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import { isDrawableVehicleRoi, VehicleRoiOverlay, type VehicleRoiConfig } from "@/components/grain/vehicle-roi-overlay";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn } from "@/lib/utils";

const VEHICLE_PLATE_CAMERA = "cam1";
const VEHICLE_PLATE_OCR_SOURCE = "main";
const VEHICLE_RUNTIME_URL = `/cameras/${VEHICLE_PLATE_CAMERA}/vehicle-plate-runtime/`;
const VEHICLE_RUNTIME_POLL_MS = 5_000;

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

export type VehiclePlateRuntime = {
  camera: string;
  enabled: boolean;
  ready: boolean;
  automation_enabled: boolean;
  camera_configured: boolean;
  source: string;
  server_push_configured: boolean;
  diagnostic: string;
  monitor: VehiclePlateMonitor | null;
  roi: VehicleRoiConfig;
};

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
  if (!runtime.automation_enabled) {
    return { label: "АВТОМАТИКА ВЫКЛ.", detail: "Автоматический запуск модели выключен.", tone: "warn" };
  }
  if (!runtime.camera_configured) {
    return {
      label: "КАМЕРА НЕ НАЗНАЧЕНА",
      detail: `${VEHICLE_PLATE_CAMERA} не добавлена в автоматическое распознавание.`,
      tone: "warn",
    };
  }
  if (runtime.source !== VEHICLE_PLATE_OCR_SOURCE) {
    return {
      label: "ПОТОК НЕ СОВПАЛ",
      detail: `AI читает ${runtime.source || "другой поток"}; ожидаемый источник распознавания — ${VEHICLE_PLATE_OCR_SOURCE}.`,
      tone: "bad",
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

function vehiclePipelineHint(monitor: VehiclePlateMonitor): string {
  if (monitor.scanned_frames === 0) return "С запуска монитор ещё не получил ни одного кадра main.";
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
  if (roi.source !== VEHICLE_PLATE_OCR_SOURCE) {
    return {
      label: "ROI ДЛЯ ДРУГОГО ПОТОКА",
      detail: `Зона сохранена для ${roi.source || "другого потока"}.`,
      tone: "bad",
    };
  }
  if (!isDrawableVehicleRoi(roi, VEHICLE_PLATE_OCR_SOURCE)) {
    return { label: "ROI ПОВРЕЖДЁН", detail: "Координаты зоны нельзя безопасно показать.", tone: "bad" };
  }
  return { label: "ROI ПОКАЗАН", detail: "Голубая область — зона контроля остановки.", tone: "good" };
}

/**
 * Live view of the fixed vehicle-plate lane used by automatic grain export.
 * CameraStream keeps the go2rtc signalling behind the authenticated endpoint;
 * this component only selects the logical stream name.
 */
export function VehiclePlateCameraWorkspace() {
  const [streamOnline, setStreamOnline] = useState(false);
  const {
    data: runtime,
    loading: runtimeLoading,
    error: runtimeError,
    reload,
  } = useApi<VehiclePlateRuntime>(VEHICLE_RUNTIME_URL);
  useVisiblePolling(reload, VEHICLE_RUNTIME_POLL_MS, !runtimeLoading);
  const aiState = vehicleAiPresentation(runtime, runtimeLoading, runtimeError);
  const roiState = roiPresentation(runtime, runtimeError);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-sky-700">Камера автоматического вывоза</p>
        <p className="mt-1 text-sm text-[var(--muted-foreground)]">
          Камера проходной показывает машину, номер которой используется для автоматического рейса.
        </p>
      </div>

      <section
        aria-label="Камера проходной на вывоз"
        className="overflow-hidden rounded-[28px] border border-slate-800 bg-[#111318] text-white shadow-[0_24px_60px_rgba(15,23,42,0.18)]"
      >
        <div className="grid lg:aspect-[44/16] lg:grid-cols-[1.55fr_0.85fr]">
          <div className="relative aspect-video min-h-72 overflow-hidden bg-black lg:aspect-auto lg:min-h-0">
            <CameraStream
              src={VEHICLE_PLATE_CAMERA}
              onStateChange={setStreamOnline}
              className="absolute inset-0 size-full object-cover"
            />
            <VehicleRoiOverlay roi={runtimeError ? null : runtime?.roi} expectedSource={VEHICLE_PLATE_OCR_SOURCE} />
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
                Камера {VEHICLE_PLATE_CAMERA} · OCR: {VEHICLE_PLATE_OCR_SOURCE}
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
                <h2 className="mt-2 text-2xl font-bold tracking-tight">Автоматический вывоз</h2>
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
              {runtime?.monitor ? (
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
                  <p className="mt-3 text-[11px] leading-4 text-white/50">{vehiclePipelineHint(runtime.monitor)}</p>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
