"use client";

import { useState, type KeyboardEvent, type PointerEvent } from "react";
import type { AxiosError } from "axios";
import { api, apiError } from "@/lib/api";
import type { VehicleRoiConfig } from "@/lib/types";
import { clampUnit, useVideoBox, videoBoxStyle } from "@/lib/use-video-box";
import { cn } from "@/lib/utils";

export type NormalizedRoiPoint = readonly [number, number];

const KEYBOARD_STEP = 0.005;
const KEYBOARD_LARGE_STEP = 0.02;
const MIN_POINTS = 3;
const MAX_POINTS = 12;

function clampCoordinate(value: number) {
  return Number(clampUnit(value).toFixed(6));
}

/**
 * The camera PC is upgraded independently from the CRM, so treat its ROI as
 * untrusted input. A partly malformed polygon must not be drawn as a different
 * physical zone over the live image.
 */
export function normalizeVehicleRoi(points: unknown): NormalizedRoiPoint[] {
  if (!Array.isArray(points) || points.length < MIN_POINTS || points.length > MAX_POINTS) return [];

  const normalized: NormalizedRoiPoint[] = [];
  for (const point of points) {
    if (Array.isArray(point) && point.length !== 2) return [];
    if (!Array.isArray(point) && (typeof point !== "object" || point === null)) return [];
    const x = Array.isArray(point) ? point[0] : (point as { x?: unknown }).x;
    const y = Array.isArray(point) ? point[1] : (point as { y?: unknown }).y;
    if (
      typeof x !== "number" ||
      typeof y !== "number" ||
      !Number.isFinite(x) ||
      !Number.isFinite(y) ||
      x < 0 ||
      x > 1 ||
      y < 0 ||
      y > 1
    )
      return [];
    normalized.push([x, y]);
  }
  return normalized;
}

export function isDrawableVehicleRoi(roi: VehicleRoiConfig | null | undefined, expectedSource: string): boolean {
  return Boolean(
    roi?.configured &&
    roi.enabled &&
    roi.source === expectedSource &&
    roi.coordinate_space === "normalized" &&
    normalizeVehicleRoi(roi.points).length,
  );
}

function polygonArea(points: readonly NormalizedRoiPoint[]) {
  let doubledArea = 0;
  for (let index = 0; index < points.length; index += 1) {
    const current = points[index];
    const next = points[(index + 1) % points.length];
    doubledArea += current[0] * next[1] - next[0] * current[1];
  }
  return Math.abs(doubledArea) / 2;
}

/** Same rule as the backend (cameras/api_views/vehicle_runtime.py): 3..12 points and a non-degenerate area. */
export function isValidRoiDraft(points: readonly NormalizedRoiPoint[]): boolean {
  return points.length >= MIN_POINTS && points.length <= MAX_POINTS && polygonArea(points) >= 0.0001;
}

type RoiSaveNotice = { message: string; tone: "success" | "warning" };

type SavedRoi = { roi: VehicleRoiConfig; appliedToMonitor: boolean };

function readSavedRoi(value: unknown, responseKey: string, source: string, enabled: boolean): SavedRoi | null {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const roi = payload[responseKey] as VehicleRoiConfig | undefined;
  if (
    payload.saved !== true ||
    typeof payload.applied_to_monitor !== "boolean" ||
    !roi ||
    // A switched-off polygon is stored but not drawn: check its shape directly.
    !isDrawableVehicleRoi({ ...roi, enabled: roi.enabled === enabled }, source) ||
    !isValidRoiDraft(normalizeVehicleRoi(roi.points))
  )
    return null;
  return { roi, appliedToMonitor: payload.applied_to_monitor };
}

/**
 * Superuser ROI editor state shared by the camera panels: draft points, the
 * PUT to the camera runtime and the notice about the live monitor refresh.
 * `saveUrl` is null while saving is not allowed (no runtime or no rights).
 */
export function useRoiEditor({
  saveUrl,
  source,
  responseKey,
  defaultPoints,
  onSaved,
}: {
  saveUrl: string | null;
  source: string;
  /** Field of the save response that carries the stored polygon. */
  responseKey: "roi" | "zone";
  defaultPoints: NormalizedRoiPoint[];
  /** Applies the stored polygon to the page and returns the notice to show. */
  onSaved: (roi: VehicleRoiConfig, appliedToMonitor: boolean) => RoiSaveNotice;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<NormalizedRoiPoint[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState<RoiSaveNotice | null>(null);
  const canSave = isValidRoiDraft(draft) && !saving;

  function start(serverPoints: unknown) {
    const points = normalizeVehicleRoi(serverPoints);
    setDraft(points.length ? points : defaultPoints);
    setError("");
    setNotice(null);
    setEditing(true);
  }

  function cancel() {
    setEditing(false);
    setDraft([]);
    setError("");
  }

  function accept(saved: SavedRoi) {
    setNotice(onSaved(saved.roi, saved.appliedToMonitor));
    setEditing(false);
    setDraft([]);
    setError("");
  }

  /**
   * Saves the draft, or `points` outside the editing mode. `enabled: false`
   * keeps the polygon on the camera PC but switches it off.
   */
  async function save({ enabled = true, points = draft }: { enabled?: boolean; points?: NormalizedRoiPoint[] } = {}) {
    if (!saveUrl || saving || !isValidRoiDraft(points)) return;
    setSaving(true);
    setError("");
    const body = { points: points.map(([x, y]) => ({ x, y })), enabled, source };
    try {
      const response = await api.put<unknown>(saveUrl, body, { timeout: 12_000 });
      const saved = readSavedRoi(response.data, responseKey, source, enabled);
      if (!saved) throw new Error("Некорректный ответ сохранения зоны");
      accept(saved);
    } catch (cause) {
      // A 503 may mean that the polygon was persisted while the live monitor
      // refresh failed. Keep that authoritative value instead of rolling back.
      const response = (cause as AxiosError<unknown>).response;
      const partial = response?.status === 503 ? readSavedRoi(response.data, responseKey, source, enabled) : null;
      if (partial) accept(partial);
      else setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  const draftRoi: VehicleRoiConfig = {
    configured: true,
    enabled: true,
    source,
    coordinate_space: "normalized",
    points: draft.map(([x, y]) => ({ x, y })),
  };

  return { editing, draft, setDraft, draftRoi, saving, error, notice, canSave, start, cancel, save };
}

/**
 * Stop-zone overlay and superuser editor. The layer is a sibling of
 * CameraStream and is resized to the actual object-cover video box (including
 * cropped overflow), so normalized camera coordinates stay aligned with the
 * pixels on screen.
 */
export function VehicleRoiOverlay({
  roi,
  expectedSource,
  editable = false,
  onPointsChange,
  reshapable = false,
  label = "ROI ОСТАНОВКИ",
  editingLabel = "ПЕРЕТАЩИТЕ ТОЧКИ",
  editorLabel = "Редактор зоны остановки",
  pointLabel = "Точка ROI",
}: {
  roi: VehicleRoiConfig | null | undefined;
  expectedSource: string;
  editable?: boolean;
  onPointsChange?: (points: NormalizedRoiPoint[]) => void;
  /** «+» на середине стороны добавляет точку; Delete или двойной клик убирает её. */
  reshapable?: boolean;
  label?: string;
  /** Метка у первой точки при правке; пустая — без метки. */
  editingLabel?: string;
  editorLabel?: string;
  pointLabel?: string;
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const [draggingIndex, setDraggingIndex] = useState<number | null>(null);
  const videoBox = useVideoBox(container);
  const points = normalizeVehicleRoi(roi?.points);
  const drawable = videoBox && isDrawableVehicleRoi(roi, expectedSource);
  const svgPoints = points.map(([x, y]) => `${x * 1000},${y * 1000}`).join(" ");
  const labelPoint = points[0];
  const labelText = editable ? editingLabel : label;

  const canInsert = editable && reshapable && points.length < MAX_POINTS;
  const canRemove = editable && reshapable && points.length > MIN_POINTS;

  function updatePoint(index: number, x: number, y: number) {
    if (!editable || !onPointsChange) return;
    onPointsChange(points.map((point, pointIndex) => (pointIndex === index ? [x, y] : point)));
  }

  function insertPoint(afterIndex: number) {
    if (!canInsert || !onPointsChange) return;
    const [x1, y1] = points[afterIndex];
    const [x2, y2] = points[(afterIndex + 1) % points.length];
    const midpoint: NormalizedRoiPoint = [clampCoordinate((x1 + x2) / 2), clampCoordinate((y1 + y2) / 2)];
    onPointsChange([...points.slice(0, afterIndex + 1), midpoint, ...points.slice(afterIndex + 1)]);
  }

  function removePoint(index: number) {
    if (!canRemove || !onPointsChange) return;
    onPointsChange(points.filter((_, pointIndex) => pointIndex !== index));
  }

  function movePointFromPointer(index: number, event: PointerEvent<HTMLButtonElement>) {
    if (draggingIndex !== index || !container) return;
    const bounds = container.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    updatePoint(
      index,
      clampCoordinate((event.clientX - bounds.left) / bounds.width),
      clampCoordinate((event.clientY - bounds.top) / bounds.height),
    );
  }

  function movePointFromKeyboard(index: number, event: KeyboardEvent<HTMLButtonElement>) {
    const point = points[index];
    if (!point) return;
    if (canRemove && (event.key === "Delete" || event.key === "Backspace")) {
      event.preventDefault();
      removePoint(index);
      return;
    }
    const step = event.shiftKey ? KEYBOARD_LARGE_STEP : KEYBOARD_STEP;
    const movement: Partial<Record<string, NormalizedRoiPoint>> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const delta = movement[event.key];
    if (!delta) return;
    event.preventDefault();
    updatePoint(index, clampCoordinate(point[0] + delta[0]), clampCoordinate(point[1] + delta[1]));
  }

  return (
    <div
      ref={setContainer}
      aria-hidden={editable ? undefined : "true"}
      aria-label={editable ? editorLabel : undefined}
      role={editable ? "group" : undefined}
      data-testid="vehicle-roi-layer"
      className={cn(
        "absolute inset-0 overflow-hidden",
        editable ? "pointer-events-auto z-[4] touch-none" : "pointer-events-none z-[1]",
      )}
      style={videoBoxStyle(videoBox)}
    >
      {drawable ? (
        <>
          <svg
            data-testid="vehicle-roi-polygon"
            className="pointer-events-none absolute inset-0 size-full overflow-visible"
            viewBox="0 0 1000 1000"
            preserveAspectRatio="none"
          >
            <polygon
              points={svgPoints}
              fill="rgba(56, 189, 248, 0.14)"
              stroke="rgb(125, 211, 252)"
              strokeWidth="3"
              strokeDasharray="10 7"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          {labelPoint && labelText ? (
            <span
              className="pointer-events-none absolute -translate-y-full rounded bg-sky-400/90 px-2 py-1 text-[9px] font-black tracking-[0.12em] text-slate-950 shadow-lg"
              style={{ left: `${labelPoint[0] * 100}%`, top: `${labelPoint[1] * 100}%` }}
            >
              {labelText}
            </span>
          ) : null}
          {editable
            ? points.map(([x, y], index) => (
                <button
                  key={index}
                  type="button"
                  aria-label={`${pointLabel} ${index + 1}`}
                  aria-pressed={draggingIndex === index}
                  className="absolute size-6 -translate-x-1/2 -translate-y-1/2 cursor-grab rounded-full border-2 border-slate-950 bg-sky-300 shadow-[0_0_0_3px_rgba(255,255,255,0.9)] outline-none active:cursor-grabbing focus-visible:ring-4 focus-visible:ring-sky-300/60"
                  style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
                  onKeyDown={(event) => movePointFromKeyboard(index, event)}
                  onPointerDown={(event) => {
                    event.preventDefault();
                    event.currentTarget.focus();
                    event.currentTarget.setPointerCapture?.(event.pointerId);
                    setDraggingIndex(index);
                  }}
                  onPointerMove={(event) => movePointFromPointer(index, event)}
                  onPointerUp={(event) => {
                    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
                      event.currentTarget.releasePointerCapture(event.pointerId);
                    }
                    setDraggingIndex(null);
                  }}
                  onPointerCancel={() => setDraggingIndex(null)}
                  onDoubleClick={() => removePoint(index)}
                />
              ))
            : null}
          {canInsert
            ? points.map(([x1, y1], index) => {
                const [x2, y2] = points[(index + 1) % points.length];
                return (
                  <button
                    key={`insert-${index}`}
                    type="button"
                    aria-label={`Добавить точку между ${index + 1} и ${((index + 1) % points.length) + 1}`}
                    title="Добавить точку"
                    className="absolute flex size-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border border-white/80 bg-slate-950/70 text-[13px] font-bold leading-none text-white opacity-70 shadow transition hover:scale-110 hover:bg-sky-500 hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-sky-300/60"
                    style={{ left: `${((x1 + x2) / 2) * 100}%`, top: `${((y1 + y2) / 2) * 100}%` }}
                    onClick={() => insertPoint(index)}
                  >
                    +
                  </button>
                );
              })
            : null}
        </>
      ) : null}
    </div>
  );
}
