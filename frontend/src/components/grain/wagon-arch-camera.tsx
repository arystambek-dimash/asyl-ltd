"use client";

import { useState, type ReactNode } from "react";
import { Check, PencilLine, X } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import {
  VehicleRoiOverlay,
  isDrawableVehicleRoi,
  useRoiEditor,
  type NormalizedRoiPoint,
} from "@/components/grain/vehicle-roi-overlay";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { formatKg } from "@/lib/grain";
import { showSuccess } from "@/lib/toast";
import type { WagonArchCameraRuntime } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useVisiblePolling } from "@/lib/use-visible-polling";
import { cn } from "@/lib/utils";
import { archStopReasonLabel } from "@/lib/weighing-evidence";
import { useAuth } from "@/store/auth";

const RUNTIME_URL = "/cameras/wagon-arch-runtime/";
const POLL_MS = 5_000;
const DEFAULT_ZONE: NormalizedRoiPoint[] = [
  [0.1, 0.3],
  [0.9, 0.3],
  [0.9, 0.7],
  [0.1, 0.7],
];

function motionLabel(runtime: WagonArchCameraRuntime | null) {
  const motion = runtime?.motion;
  if (!motion || motion.state === "unknown") return "Нет данных о движении";
  if (motion.state === "moving") return "Вагон едет";
  return `Вагон стоит · ${Math.round(motion.still_seconds)} с`;
}

function collectorLabel(runtime: WagonArchCameraRuntime | null) {
  const collector = runtime?.runtime.collector;
  if (!collector) return "Сборщик: нет данных";
  const status: Record<string, string> = {
    running: "работает",
    hardware_unavailable: "нет связи с весами",
    camera_unavailable: "нет связи с ПК камер",
    starting: "запускается",
  };
  return `Сборщик: ${status[collector.status] ?? collector.status}${collector.pending ? ` · в очереди ${collector.pending}` : ""}`;
}

type WagonArchCameraPanelProps = {
  /** Камера, закреплённая за проходной вагонов (настройка «Назначить камеру»). */
  assignedCamera?: string | null;
  /** Кнопка «Назначить камеру» — рендерится рядом с действиями зоны. */
  assignAction?: ReactNode;
};

export function WagonArchCameraPanel({ assignedCamera = null, assignAction = null }: WagonArchCameraPanelProps = {}) {
  const canManage = useAuth((state) => Boolean(state.me?.is_superuser));
  const { data: runtime, error, reload, setData } = useApi<WagonArchCameraRuntime>(RUNTIME_URL);
  const [streamOnline, setStreamOnline] = useState(false);
  const editor = useRoiEditor({
    saveUrl: canManage && runtime ? `/cameras/${runtime.camera}/wagon-arch-runtime/` : null,
    source: "main",
    responseKey: "zone",
    defaultPoints: DEFAULT_ZONE,
    onSaved: (savedZone, appliedToMonitor) => {
      if (runtime) setData({ ...runtime, zone: savedZone });
      if (!appliedToMonitor) {
        return {
          message: "Зона сохранена, но монитор пока не подтвердил обновление. Он перечитает зону после восстановления.",
          tone: "warning",
        };
      }
      showSuccess("Зона арки сохранена");
      return { message: "Зона сохранена. ПК камер применит её в течение пары секунд.", tone: "success" };
    },
  });
  const { editing, saving } = editor;
  useVisiblePolling(reload, POLL_MS, !editing && !saving);

  const zone = runtime?.zone ?? null;
  const overlay = editing ? editor.draftRoi : zone;
  const zoneConfigured = Boolean(zone && isDrawableVehicleRoi(zone, "main"));
  const lastStop = runtime?.runtime.last_stop ?? null;
  // Пока прокси не ответил, показываем поток закреплённой камеры — оператор видит видео сразу.
  const streamSrc = runtime?.stream ?? assignedCamera;
  const cameraMismatch = Boolean(assignedCamera && runtime && assignedCamera !== runtime.camera);

  function startEditing() {
    if (!canManage || !runtime) return;
    editor.start(runtime.zone.points);
  }

  return (
    <Card role="region" aria-label="Зона арки вагонных весов" className="space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Зона арки</h2>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Номер вагона читается с этой камеры на проходной. Вагон встал в зоне и вес устоялся — записывается заезд;
            поехал — выезд.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {assignAction}
          {canManage && editing ? (
            <div className="flex flex-wrap items-center gap-2" aria-label="Действия редактора зоны">
              <Button variant="outline" disabled={saving} onClick={editor.cancel}>
                <X className="size-4" /> Отмена
              </Button>
              <Button disabled={!editor.canSave} onClick={() => void editor.save()}>
                <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить зону"}
              </Button>
            </div>
          ) : canManage ? (
            <Button variant="outline" disabled={!runtime || Boolean(error)} onClick={startEditing}>
              <PencilLine className="size-4" /> Изменить зону
            </Button>
          ) : null}
        </div>
      </div>
      {error && <ErrorAlert message={error} onRetry={() => void reload()} />}
      {runtime?.diagnostic ? (
        <p
          role="alert"
          className="rounded-md border border-[var(--warning)]/30 bg-[var(--warning)]/10 px-3 py-2 text-sm"
        >
          {runtime.diagnostic}
        </p>
      ) : null}
      {editing && (
        <p role="status" className="text-sm text-[var(--muted-foreground)]">
          Перетащите точки мышью или выберите точку клавишей Tab и двигайте стрелками. Shift + стрелка — крупный шаг.
        </p>
      )}
      {editor.error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {editor.error}
        </p>
      )}
      {editor.notice && (
        <p
          role="status"
          className={cn(
            "text-sm",
            editor.notice.tone === "success" ? "text-[var(--success)]" : "text-[var(--warning)]",
          )}
        >
          {editor.notice.message}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[1.55fr_0.85fr]">
        <div className="relative aspect-video overflow-hidden rounded-lg bg-[#141416]">
          {streamSrc ? (
            <CameraStream
              key={streamSrc}
              src={streamSrc}
              onStateChange={setStreamOnline}
              className="absolute inset-0 size-full object-contain"
            />
          ) : null}
          <VehicleRoiOverlay
            roi={overlay}
            expectedSource="main"
            editable={editing}
            onPointsChange={editor.setDraft}
            label="ЗОНА АРКИ"
            editorLabel="Редактор зоны арки"
            pointLabel="Точка зоны"
          />
          {!streamOnline && !editing && (
            <span className="absolute left-3 top-3 z-[2] text-[11px] text-white/70">Подключаем видеопоток…</span>
          )}
        </div>
        <dl className="grid content-start gap-3 text-sm">
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Камера</dt>
            <dd className="font-medium">{assignedCamera ?? "не назначена"}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Зона</dt>
            <dd className="font-medium">{!runtime ? "—" : zoneConfigured ? "Задана" : "Зона арки не задана"}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Движение</dt>
            <dd className="font-medium">{motionLabel(runtime)}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Контур</dt>
            <dd className="font-medium">{collectorLabel(runtime)}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 border-b border-[var(--border)] py-2">
            <dt className="text-[var(--muted-foreground)]">Автоматика</dt>
            <dd className="font-medium">{!runtime ? "—" : runtime.automation_enabled ? "включена" : "выключена"}</dd>
          </div>
          {lastStop && (
            <div className="py-2">
              <dt className="text-[var(--muted-foreground)]">Последняя стоянка</dt>
              <dd className="mt-1">
                <span className="font-medium">{lastStop.number ? `Вагон ${lastStop.number}` : "Вагон без номера"}</span>{" "}
                <span className="tabular-nums">{formatKg(lastStop.full_weight_kg)}</span>
                {lastStop.exit_weight_kg != null && (
                  <span className="tabular-nums"> → {formatKg(lastStop.exit_weight_kg)}</span>
                )}
                {lastStop.blocked_reason && (
                  <p className="mt-1 text-[var(--warning)]">
                    {archStopReasonLabel(lastStop.blocked_reason, lastStop.blocked_detail)}
                  </p>
                )}
              </dd>
            </div>
          )}
          {cameraMismatch && runtime && (
            <p role="alert" className="text-[var(--warning)]">
              Камера номеров ({assignedCamera}) и камера арки ({runtime.camera}) должны совпадать.
            </p>
          )}
          <p className="text-xs leading-5 text-[var(--muted-foreground)]">
            Эта камера отвечает только за номера вагонов на проходной. Камеры погрузки остаются в моноблоке.
          </p>
        </dl>
      </div>
    </Card>
  );
}
