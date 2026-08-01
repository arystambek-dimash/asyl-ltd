import { MAP_PALETTE as P } from "../palette";

/**
 * Маркер CV-камеры. Рисуется в центре бокса зоны (обычно 48×48).
 * Если в поле «Назначение» зоны указан источник потока — при наведении
 * карта показывает живое превью. Замена графики: перерисуйте <g>.
 * Промпты: ../SVG_PROMPTS.md
 */
export function CameraAsset({ width, height, hasStream }: { width: number; height: number; hasStream: boolean }) {
  return (
    <g transform={`translate(${width / 2} ${height / 2})`}>
      <circle r="15" fill={P.cameraHalo} opacity=".16" />
      <rect x="-9" y="-6" width="14" height="10" rx="3" fill={P.cameraBody} />
      <path d="M 5 -4 L 12 -7 V 5 L 5 2 Z" fill={P.cameraBody} />
      <circle cx="10" cy="-10" r="3.2" fill={hasStream ? P.online : P.warning}>
        <animate attributeName="opacity" values="1;.25;1" dur="1.8s" repeatCount="indefinite" />
      </circle>
    </g>
  );
}
