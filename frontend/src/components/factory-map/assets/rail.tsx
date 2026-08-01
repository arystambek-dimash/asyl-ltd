import { MAP_PALETTE as P } from "../palette";

/**
 * Участок железнодорожного пути: рельсы и шпалы, растягивается по ширине бокса.
 * Основная ж/д ветка сцены рисуется фоном карты — этот ассет для
 * дополнительных путей. Промпты: ../SVG_PROMPTS.md
 */
export function RailAsset({ width, height, name }: { width: number; height: number; name: string }) {
  const centerY = height / 2;
  const railGap = Math.min(18, height * 0.36);

  return (
    <g>
      <text x="4" y="-8" fontSize="10.5" fill={P.textMuted}>
        {name}
      </text>
      <rect width={width} height={height} fill={P.grassDark} opacity=".6" rx="6" />
      {Array.from({ length: Math.floor(width / 24) }).map((_, index) => (
        <line
          key={index}
          x1={10 + index * 24}
          y1={centerY - railGap / 2 - 5}
          x2={10 + index * 24}
          y2={centerY + railGap / 2 + 5}
          stroke={P.asphaltDark}
          strokeWidth="2"
          opacity=".7"
        />
      ))}
      <line
        x1="0"
        y1={centerY - railGap / 2}
        x2={width}
        y2={centerY - railGap / 2}
        stroke={P.asphaltDark}
        strokeWidth="3"
      />
      <line
        x1="0"
        y1={centerY + railGap / 2}
        x2={width}
        y2={centerY + railGap / 2}
        stroke={P.asphaltDark}
        strokeWidth="3"
      />
    </g>
  );
}
