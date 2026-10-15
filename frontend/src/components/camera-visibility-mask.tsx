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
 * Закрывает кадр вне зоны видимости камеры — там же, где кадр закрыт для AI
 * на ПК камер. Слой — сосед CameraStream и подгоняется под реальный бокс
 * видео (object-cover / object-contain), поэтому полигон совпадает с картинкой.
 * Ставить сразу после видео: без z-index слой закрывает только кадр, а
 * подписи и кнопки, идущие следом, остаются поверх.
 * `preview` только затемняет: в редакторе видно, что окажется за маской.
 */
export function CameraVisibilityMask({
  zone,
  points,
  preview = false,
}: {
  zone?: VehicleRoiConfig | null;
  /** Черновик редактора вместо сохранённой зоны. */
  points?: readonly NormalizedRoiPoint[];
  preview?: boolean;
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
            fill={preview ? "rgba(2, 6, 23, 0.62)" : "#0d0e11"}
          />
          {!preview && (
            <polygon
              points={svgPoints(polygon).join(" ")}
              fill="none"
              stroke="rgba(255, 255, 255, 0.16)"
              strokeWidth="1"
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
      ) : null}
    </div>
  );
}
