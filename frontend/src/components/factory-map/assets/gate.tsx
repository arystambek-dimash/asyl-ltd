import { DEFS, MAP_PALETTE as P } from "../palette";

/**
 * КПП: будка охраны и шлагбаум. Нативный холст 130×92.
 * Замена графики: перерисуйте содержимое scale-группы. Промпты: ../SVG_PROMPTS.md
 */
const ART_W = 130;
const ART_H = 92;

export function GateAsset({ width, height, name }: { width: number; height: number; name: string }) {
  const scale = Math.min(width / ART_W, height / ART_H);
  const offsetX = 0;
  const offsetY = (height - ART_H * scale) / 2;

  return (
    <g>
      <text x={width / 2} y="-9" textAnchor="middle" fontSize="12" fontWeight="600" fill={P.textMuted}>
        {name}
      </text>
      <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
        <rect
          width="54"
          height="44"
          rx="8"
          fill={`url(#${DEFS.roof})`}
          stroke={P.border}
          filter={`url(#${DEFS.soft})`}
        />
        <rect x="10" y="12" width="14" height="12" rx="2" fill="#dbe7fb" />
        <rect x="32" y="12" width="14" height="12" rx="2" fill="#dbe7fb" />
        {/* шлагбаум */}
        <g transform="translate(50 56)">
          <rect x="-4" y="-4" width="8" height="12" rx="2" fill="#78909c" />
          <g>
            <animateTransform
              attributeName="transform"
              type="rotate"
              values="0 0 0;0 0 0;-64 0 0;-64 0 0;0 0 0"
              keyTimes="0;0.55;0.65;0.9;1"
              dur="14s"
              repeatCount="indefinite"
            />
            <rect y="-3" width="74" height="6" rx="3" fill="#ef5350" />
            <rect x="10" y="-3" width="12" height="6" fill="#ffffff" />
            <rect x="34" y="-3" width="12" height="6" fill="#ffffff" />
            <rect x="58" y="-3" width="12" height="6" fill="#ffffff" />
          </g>
        </g>
      </g>
    </g>
  );
}
