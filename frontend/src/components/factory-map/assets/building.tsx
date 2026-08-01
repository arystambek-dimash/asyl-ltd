import type { ReactNode } from "react";
import { DEFS } from "../palette";

/**
 * Базовый корпус здания: крыша-подложка, цветная шапка с названием, окна.
 * Растягивается на весь бокс зоны — из него собраны столовая, офис,
 * лаборатория и служебные постройки.
 *
 * Замена графики на сгенерированную: вставьте свои <path>/<g> вместо
 * содержимого — контракт ассета один и тот же: рисуем в (0,0)…(width,height).
 * Промпты: frontend/src/components/factory-map/SVG_PROMPTS.md
 */
export function BuildingFrame({
  width,
  height,
  name,
  headerFill,
  headerText,
  windows = 3,
  children,
}: {
  width: number;
  height: number;
  name: string;
  headerFill: string;
  headerText: string;
  windows?: number;
  children?: ReactNode;
}) {
  const header = Math.min(26, height * 0.24);
  const windowTop = header + Math.min(18, height * 0.12);
  const windowWidth = Math.min(38, (width - 40) / Math.max(1, windows) - 10);
  const showWindows = windows > 0 && height >= 84 && windowWidth >= 18;
  const label = name.length > Math.floor(width / 6.6) ? `${name.slice(0, Math.floor(width / 6.6) - 1)}…` : name;

  return (
    <g>
      <rect
        width={width}
        height={height}
        rx="14"
        fill={`url(#${DEFS.roof})`}
        stroke="#d4d9e0"
        filter={`url(#${DEFS.soft})`}
      />
      <rect width={width} height={header} rx={header / 2} fill={headerFill} />
      <text
        x={width / 2}
        y={header / 2 + 4.5}
        textAnchor="middle"
        fontSize="13"
        fontWeight="650"
        fill={headerText}
        style={{ pointerEvents: "none" }}
      >
        {label}
      </text>
      {showWindows && (
        <g fill="#ffffff" opacity=".8">
          {Array.from({ length: windows }).map((_, index) => (
            <rect
              key={index}
              x={20 + index * (windowWidth + 12)}
              y={windowTop}
              width={windowWidth}
              height={Math.min(26, height * 0.2)}
              rx="5"
            />
          ))}
        </g>
      )}
      {children}
    </g>
  );
}
