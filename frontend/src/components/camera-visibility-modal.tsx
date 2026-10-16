"use client";

import { useEffect, useState } from "react";
import { Check, Eye, EyeOff, LoaderCircle, Maximize, PencilLine, ShieldCheck, X } from "lucide-react";
import { CameraFrameStage } from "@/components/camera-frame-stage";
import { CameraVisibilityMask, visibilityZoneActive } from "@/components/camera-visibility-mask";
import {
  VehicleRoiOverlay,
  isValidRoiDraft,
  normalizeVehicleRoi,
  useRoiEditor,
  type NormalizedRoiPoint,
} from "@/components/grain/vehicle-roi-overlay";
import { Button } from "@/components/ui/button";
import { ErrorAlert, FormError } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import type { VehicleRoiConfig } from "@/lib/types";
import { cn } from "@/lib/utils";

/** Новая зона и «Весь кадр» — кадр целиком, до самых краёв: иначе ИИ потерял бы
 * полосу у каждой кромки. Углы тянут внутрь. */
const FULL_FRAME: NormalizedRoiPoint[] = [
  [0, 0],
  [1, 0],
  [1, 1],
  [0, 1],
];

/**
 * Зона видимости одной камеры: всё вне полигона закрыто для AI на ПК камер,
 * экраны CRM показывают кадр целиком. Монтируется на время правки (с key по камере).
 */
export function CameraVisibilityModal({
  camera,
  onClose,
  onZoneChange,
}: {
  camera: { src: string; zone: string; visibility_zone?: VehicleRoiConfig | null };
  onClose: () => void;
  /** Сохранённая зона изменилась: обновить инвентарь камер. */
  onZoneChange: (src: string, zone: VehicleRoiConfig) => void;
}) {
  const { src } = camera;
  const url = `/cameras/${encodeURIComponent(src)}/visibility-zone/`;
  const [zone, setZone] = useState<VehicleRoiConfig | null>(camera.visibility_zone ?? null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [loadVersion, setLoadVersion] = useState(0);
  const editor = useRoiEditor({
    saveUrl: loading || loadError ? null : url,
    source: "main",
    responseKey: "zone",
    defaultPoints: FULL_FRAME,
    onSaved: (saved, appliedToMonitor) => {
      setZone(saved);
      onZoneChange(src, saved);
      if (!appliedToMonitor) {
        return {
          message: "Зона сохранена, но ПК камер пока не подтвердил применение. Проверьте камеру через минуту.",
          tone: "warning",
        };
      }
      return saved.enabled
        ? { message: "Зона сохранена: AI видит только выделенную область, на экранах кадр целиком.", tone: "success" }
        : { message: "Маска выключена: AI снова видит весь кадр.", tone: "success" };
    },
  });
  const { editing, saving } = editor;
  const active = visibilityZoneActive(zone);
  const savedPoints = normalizeVehicleRoi(zone?.points);
  const canToggle = !loading && !loadError && !!zone?.configured && isValidRoiDraft(savedPoints);

  // Сохранённая на ПК камер зона главнее копии из инвентаря.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    api.get<VehicleRoiConfig>(url, { timeout: 10_000 }).then(
      (response) => {
        if (cancelled) return;
        setZone(response.data);
        onZoneChange(src, response.data);
        setLoading(false);
      },
      (cause) => {
        if (cancelled) return;
        setLoadError(apiError(cause));
        setLoading(false);
      },
    );
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- компонент монтируется на одну камеру
  }, [loadVersion]);

  const hint = editing
    ? "Перетащите точки · «+» на стороне добавляет точку · двойной клик убирает"
    : active
      ? "Затемнённое AI не видит; на экранах кадр целиком"
      : "Маска выключена — AI видит весь кадр";

  return (
    <Modal
      open
      onClose={onClose}
      dismissible={editing ? "escape" : true}
      eyebrow="Только для суперпользователя"
      title="Зона видимости камеры"
      description={`${camera.zone} · всё вне зоны не попадает в AI. На экранах камера видна целиком.`}
      className="max-w-4xl"
      footer={
        <>
          <div className="mr-auto hidden items-center gap-2 text-xs text-[var(--muted-foreground)] sm:flex">
            <ShieldCheck className="size-4 text-emerald-600" />
            Настройка защищена правами superuser
          </div>
          {editing ? (
            <>
              <Button variant="ghost" disabled={saving} onClick={editor.cancel}>
                <X className="size-4" /> Отмена
              </Button>
              <Button
                disabled={!editor.canSave || loading || !!loadError}
                onClick={() => void editor.save()}
                className="min-w-36 bg-sky-600 text-white hover:bg-sky-700"
              >
                {saving ? <LoaderCircle className="size-4 animate-spin" /> : <Check className="size-4" />}
                {saving ? "Сохранение…" : "Сохранить зону"}
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" onClick={onClose}>
                Закрыть
              </Button>
              {canToggle && (
                <Button
                  variant="outline"
                  disabled={saving}
                  onClick={() => void editor.save({ enabled: !active, points: savedPoints })}
                >
                  {active ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                  {active ? "Выключить маску" : "Включить маску"}
                </Button>
              )}
              <Button
                disabled={loading || !!loadError || saving}
                onClick={() => editor.start(zone?.configured ? zone.points : null)}
                className="bg-sky-600 text-white hover:bg-sky-700"
              >
                <PencilLine className="size-4" /> {zone?.configured ? "Изменить зону" : "Задать зону"}
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="space-y-4">
        <CameraFrameStage src={src} hint={hint} hintColor="#7dd3fc">
          {editing ? (
            <>
              <CameraVisibilityMask points={editor.draft} />
              <VehicleRoiOverlay
                roi={editor.draftRoi}
                expectedSource="main"
                editable
                reshapable
                onPointsChange={editor.setDraft}
                editingLabel=""
                editorLabel="Редактор зоны видимости"
                pointLabel="Точка зоны"
              />
            </>
          ) : (
            <CameraVisibilityMask zone={zone} />
          )}
        </CameraFrameStage>

        {editing && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={saving}
              onClick={() => editor.setDraft(FULL_FRAME)}
            >
              <Maximize className="size-4" /> Весь кадр
            </Button>
            <span className="text-xs text-[var(--muted-foreground)]">
              {editor.draft.length} из 12 точек · Tab и стрелки двигают точку, Delete убирает
            </span>
          </div>
        )}

        {loading && (
          <div className="flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
            <LoaderCircle className="size-4 animate-spin" /> Загружаем сохранённую зону…
          </div>
        )}
        {loadError && <ErrorAlert message={loadError} onRetry={() => setLoadVersion((version) => version + 1)} />}
        {editor.notice && (
          <p
            role="status"
            className={cn(
              "rounded-lg border px-4 py-3 text-sm font-medium",
              editor.notice.tone === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-amber-200 bg-amber-50 text-amber-900",
            )}
          >
            {editor.notice.message}
          </p>
        )}
        <FormError message={editor.error} className="rounded-lg px-4 py-3" />
      </div>
    </Modal>
  );
}
