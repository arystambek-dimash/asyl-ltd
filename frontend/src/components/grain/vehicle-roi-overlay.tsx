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

function clampCoordinate(value: number) {
  return Number(clampUnit(value).toFixed(6));
}

/**
 * The camera PC is upgraded independently from the CRM, so treat its ROI as
 * untrusted input. A partly malformed polygon must not be drawn as a different
 * physical zone over the live image.
 */
export function normalizeVehicleRoi(points: unknown): NormalizedRoiPoint[] {
  if (!Array.isArray(points) || points.length < 3 || points.length > 12) return [];

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
  return points.length >= 3 && points.length <= 12 && polygonArea(points) >= 0.0001;
}

type RoiSaveNotice = { message: string; tone: "success" | "warning" };

type SavedRoi = { roi: VehicleRoiConfig; appliedToMonitor: boolean };

function readSavedRoi(value: unknown, responseKey: string, source: string): SavedRoi | null {
  if (!value || typeof value !== "object") return null;
  const payload = value as Record<string, unknown>;
  const roi = payload[responseKey] as VehicleRoiConfig | undefined;
  if (
    payload.saved !== true ||
    typeof payload.applied_to_monitor !== "boolean" ||
    !roi ||
    !isDrawableVehicleRoi(roi, source) ||
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

  async function save() {
    if (!saveUrl || !canSave) return;
    setSaving(true);
    setError("");
    const body = { points: draft.map(([x, y]) => ({ x, y })), enabled: true, source };
    try {
      const response = await api.put<unknown>(saveUrl, body, { timeout: 12_000 });
      const saved = readSavedRoi(response.data, responseKey, source);
      if (!saved) throw new Error("Некорректный ответ сохранения зоны");
      accept(saved);
    } catch (cause) {
      // A 503 may mean that the polygon was persisted while the live monitor
      // refresh failed. Keep that authoritative value instead of rolling back.
      const response = (cause as AxiosError<unknown>).response;
      const partial = response?.status === 503 ? readSavedRoi(response.data, responseKey, source) : null;
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
  label = "ROI ОСТАНОВКИ",
  editorLabel = "Редактор зоны остановки",
  pointLabel = "Точка ROI",
}: {
  roi: VehicleRoiConfig | null | undefined;
  expectedSource: string;
  editable?: boolean;
  onPointsChange?: (points: NormalizedRoiPoint[]) => void;
  label?: string;
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

  function updatePoint(index: number, x: number, y: number) {
    if (!editable || !onPointsChange) return;
    onPointsChange(points.map((point, pointIndex) => (pointIndex === index ? [x, y] : point)));
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
          {labelPoint ? (
            <span
              className="pointer-events-none absolute -translate-y-full rounded bg-sky-400/90 px-2 py-1 text-[9px] font-black tracking-[0.12em] text-slate-950 shadow-lg"
              style={{ left: `${labelPoint[0] * 100}%`, top: `${labelPoint[1] * 100}%` }}
            >
              {editable ? "ПЕРЕТАЩИТЕ ТОЧКИ" : label}
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
                />
              ))
            : null}
        </>
      ) : null}
    </div>
  );
}
