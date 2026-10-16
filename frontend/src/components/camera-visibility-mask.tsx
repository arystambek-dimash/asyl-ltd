"use client";

import { useState } from "react";
import {
  isDrawableVehicleRoi,
  normalizeVehicleRoi,
  type NormalizedRoiPoint,
} from "@/components/grain/vehicle-roi-overlay";
import type { VehicleRoiConfig } from "@/lib/types";
import { useVideoBox, videoBoxStyle } from "@/lib/use-video-box";

/** Зона видимости включена и годится для отрисовки. */
export function visibilityZoneActive(zone: VehicleRoiConfig | null | undefined): boolean {
  return isDrawableVehicleRoi(zone, "main");
}

const svgPoints = (points: readonly NormalizedRoiPoint[]) => points.map(([x, y]) => `${x * 1000},${y * 1000}`);

/**
 * Показывает в редакторах, что AI не увидит: кадр вне зоны видимости
 * затемнён, зона обведена. Маска действует только на AI (её применяет ПК
 * камер), поэтому на обычных экранах этот слой не ставят — люди видят кадр
 * целиком. Слой — сосед видео и подгоняется под его реальный бокс
 * (object-cover / object-contain); ставить сразу после видео, без z-index.
 */
export function CameraVisibilityMask({
  zone,
  points,
}: {
  zone?: VehicleRoiConfig | null;
  /** Черновик редактора вместо сохранённой зоны. */
  points?: readonly NormalizedRoiPoint[];
}) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const videoBox = useVideoBox(container);
  const polygon = points ?? (visibilityZoneActive(zone) ? normalizeVehicleRoi(zone?.points) : []);

  return (
    <div
      ref={setContainer}
      aria-hidden="true"
      data-testid="camera-visibility-mask"
      className="pointer-events-none absolute inset-0 overflow-hidden"
      style={videoBoxStyle(videoBox)}
    >
      {videoBox && polygon.length >= 3 ? (
        <svg className="absolute inset-0 size-full" viewBox="0 0 1000 1000" preserveAspectRatio="none">
          {/* Весь кадр минус полигон: evenodd вырезает зону из заливки. */}
          <path
            d={`M0,0 H1000 V1000 H0 Z M${svgPoints(polygon).join(" L")} Z`}
            fillRule="evenodd"
            fill="rgba(2, 6, 23, 0.62)"
          />
          {/* Черновик обводит сам редактор точек; сохранённую зону — этот слой. */}
          {!points && (
            <polygon
              points={svgPoints(polygon).join(" ")}
              fill="none"
              stroke="rgba(167, 139, 250, 0.9)"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
      ) : null}
    </div>
  );
}
