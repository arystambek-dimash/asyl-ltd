"use client";

import { useState } from "react";
import type { AxiosError } from "axios";
import { Check, PencilLine, X } from "lucide-react";
import { CameraStream } from "@/components/camera-stream";
import {
  VehicleRoiOverlay,
  isDrawableVehicleRoi,
  normalizeVehicleRoi,
  type NormalizedRoiPoint,
  type VehicleRoiConfig,
} from "@/components/grain/vehicle-roi-overlay";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ErrorAlert } from "@/components/ui/data-state";
import { api, apiError } from "@/lib/api";
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

type SaveResponse = { saved: boolean; applied_to_monitor: boolean; zone: VehicleRoiConfig; code?: string };

function draftZone(points: NormalizedRoiPoint[]): VehicleRoiConfig {
  return {
    configured: true,
    enabled: true,
    source: "main",
    coordinate_space: "normalized",
    points: points.map(([x, y]) => ({ x, y })),
  };
}

function polygonArea(points: NormalizedRoiPoint[]) {
  let area = 0;
  for (let i = 0; i < points.length; i += 1) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[(i + 1) % points.length];
    area += x1 * y2 - x2 * y1;
  }
  return Math.abs(area) / 2;
}

function validDraft(points: NormalizedRoiPoint[]) {
  return points.length >= 3 && points.length <= 12 && polygonArea(points) >= 0.0001;
}

function acceptedSave(value: unknown): value is SaveResponse {
  return (
    typeof value === "object" &&
    value !== null &&
    (value as SaveResponse).saved === true &&
    typeof (value as SaveResponse).applied_to_monitor === "boolean" &&
    isDrawableVehicleRoi((value as SaveResponse).zone, "main")
  );
}

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

export function WagonArchCameraPanel() {
  const canManage = useAuth((state) => Boolean(state.me?.is_superuser));
  const { data: runtime, error, reload, setData } = useApi<WagonArchCameraRuntime>(RUNTIME_URL);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<NormalizedRoiPoint[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [notice, setNotice] = useState<{ message: string; tone: "success" | "warning" } | null>(null);
  const [streamOnline, setStreamOnline] = useState(false);
  useVisiblePolling(reload, POLL_MS, !editing && !saving);

  const zone = runtime?.zone ?? null;
  const overlay = editing ? draftZone(draft) : zone;
  const zoneConfigured = Boolean(zone && isDrawableVehicleRoi(zone, "main"));
  const canSave = validDraft(draft) && !saving;
  const lastStop = runtime?.runtime.last_stop ?? null;

  function startEditing() {
    if (!canManage || !runtime) return;
    const points = normalizeVehicleRoi(runtime.zone.points);
    setDraft(points.length ? points : DEFAULT_ZONE);
    setSaveError("");
    setNotice(null);
    setEditing(true);
  }

  function accept(payload: SaveResponse) {
    if (runtime) setData({ ...runtime, zone: payload.zone });
    setEditing(false);
    setDraft([]);
    setSaveError("");
    setNotice(
      payload.applied_to_monitor
        ? { message: "Зона сохранена. ПК камер применит её в течение пары секунд.", tone: "success" }
        : {
            message:
              "Зона сохранена, но монитор пока не подтвердил обновление. Он перечитает зону после восстановления.",
            tone: "warning",
          },
    );
    if (payload.applied_to_monitor) showSuccess("Зона арки сохранена");
  }

  async function save() {
    if (!canManage || !runtime || !canSave) return;
    setSaving(true);
    setSaveError("");
    const body = { points: draft.map(([x, y]) => ({ x, y })), enabled: true, source: "main" };
    try {
      const response = await api.put<SaveResponse>(`/cameras/${runtime.camera}/wagon-arch-runtime/`, body, {
        timeout: 12_000,
      });
      if (!acceptedSave(response.data)) throw new Error("Некорректный ответ сохранения зоны");
      accept(response.data);
    } catch (cause) {
      // A 503 may mean the polygon was persisted while the live monitor refresh
      // failed. Keep that authoritative value instead of rolling back.
      const response = (cause as AxiosError<unknown>).response;
      if (response?.status === 503 && acceptedSave(response.data)) accept(response.data);
      else setSaveError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card role="region" aria-label="Зона арки вагонных весов" className="space-y-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Зона арки</h2>
          <p className="mt-1 text-sm text-[var(--muted-foreground)]">
            Вагон встал в зоне и вес устоялся — записывается заезд; поехал — выезд.
          </p>
        </div>
        {canManage && editing ? (
          <div className="flex flex-wrap items-center gap-2" aria-label="Действия редактора зоны">
            <Button
              variant="outline"
              disabled={saving}
              onClick={() => {
                setEditing(false);
                setDraft([]);
                setSaveError("");
              }}
            >
              <X className="size-4" /> Отмена
            </Button>
            <Button disabled={!canSave} onClick={() => void save()}>
              <Check className="size-4" /> {saving ? "Сохранение…" : "Сохранить зону"}
            </Button>
          </div>
        ) : canManage ? (
          <Button variant="outline" disabled={!runtime || Boolean(error)} onClick={startEditing}>
            <PencilLine className="size-4" /> Изменить зону
          </Button>
        ) : null}
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
      {saveError && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {saveError}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className={cn("text-sm", notice.tone === "success" ? "text-[var(--success)]" : "text-[var(--warning)]")}
        >
          {notice.message}
        </p>
      )}
      <div className="grid gap-4 lg:grid-cols-[1.55fr_0.85fr]">
        <div className="relative aspect-video overflow-hidden rounded-lg bg-[#141416]">
          {runtime?.stream ? (
            <CameraStream
              key={runtime.stream}
              src={runtime.stream}
              onStateChange={setStreamOnline}
              className="absolute inset-0 size-full object-contain"
            />
          ) : null}
          <VehicleRoiOverlay
            roi={overlay}
            expectedSource="main"
            editable={editing}
            onPointsChange={setDraft}
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
        </dl>
      </div>
    </Card>
  );
}
