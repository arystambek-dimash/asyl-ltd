"use client";

import { useState } from "react";
import { useVideoBox } from "@/lib/use-video-box";

export type VehicleRoiConfig = {
  configured: boolean;
  enabled: boolean;
  source: string;
  coordinate_space: string;
  points: unknown;
  updated_at?: string | null;
};

type RoiPoint = readonly [number, number];

/**
 * The camera PC is upgraded independently from the CRM, so treat its ROI as
 * untrusted input. A partly malformed polygon must not be drawn as a different
 * physical zone over the live image.
 */
export function normalizeVehicleRoi(points: unknown): RoiPoint[] {
  if (!Array.isArray(points) || points.length < 3 || points.length > 12) return [];

  const normalized: RoiPoint[] = [];
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

/**
 * Read-only stop-zone overlay. The layer is a sibling of CameraStream and is
 * resized to the actual object-cover video box (including cropped overflow),
 * so normalized camera coordinates stay aligned with the pixels on screen.
 */
export function VehicleRoiOverlay({
  roi,
  expectedSource,
}: {
  roi: VehicleRoiConfig | null | undefined;
  expectedSource: string;
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const videoBox = useVideoBox(container);
  const points = normalizeVehicleRoi(roi?.points);
  const drawable = videoBox && isDrawableVehicleRoi(roi, expectedSource);
  const svgPoints = points.map(([x, y]) => `${x * 1000},${y * 1000}`).join(" ");
  const labelPoint = points[0];

  return (
    <div
      ref={setContainer}
      aria-hidden="true"
      data-testid="vehicle-roi-layer"
      className="pointer-events-none absolute inset-0 z-[1] overflow-hidden"
      style={
        videoBox
          ? {
              left: videoBox.left,
              top: videoBox.top,
              width: videoBox.width,
              height: videoBox.height,
              right: "auto",
              bottom: "auto",
            }
          : undefined
      }
    >
      {drawable ? (
        <>
          <svg
            data-testid="vehicle-roi-polygon"
            className="absolute inset-0 size-full overflow-visible"
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
              className="absolute -translate-y-full rounded bg-sky-400/90 px-2 py-1 text-[9px] font-black tracking-[0.12em] text-slate-950 shadow-lg"
              style={{ left: `${labelPoint[0] * 100}%`, top: `${labelPoint[1] * 100}%` }}
            >
              ROI ОСТАНОВКИ
            </span>
          ) : null}
        </>
      ) : null}
    </div>
  );
}
