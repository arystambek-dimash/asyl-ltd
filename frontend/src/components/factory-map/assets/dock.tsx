import { DEFS, MAP_PALETTE as P } from "../palette";

/**
 * Пост погрузки автотранспорта: площадка, лента и грузовик под загрузкой.
 * Нативный холст 230×132. Замена графики: перерисуйте scale-группу.
 * Промпты: ../SVG_PROMPTS.md
 */
const ART_W = 230;
const ART_H = 132;

export function DockAsset({ width, height, name }: { width: number; height: number; name: string }) {
  const scale = Math.min(width / ART_W, height / ART_H);
  const offsetX = (width - ART_W * scale) / 2;
  const offsetY = (height - ART_H * scale) / 2;

  return (
    <g>
      <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
        <rect width="215" height="104" y="8" rx="12" fill={P.asphalt} opacity=".85" />
        {/* лента со склада */}
        <path d="M 214 52 H 122" stroke="#9aa5b1" strokeWidth="22" strokeLinecap="round" />
        <path d="M 214 52 H 122" stroke="#39424e" strokeWidth="15" strokeLinecap="round" />
        <path d="M 214 52 H 122" stroke="#cdd5de" strokeWidth="15" strokeLinecap="round" strokeDasharray="5 12" opacity=".8">
          <animate attributeName="stroke-dashoffset" from="0" to="102" dur="2s" repeatCount="indefinite" />
        </path>
        <rect x="-9" y="-7" width="18" height="14" rx="4" fill={P.bag} stroke={P.bagStroke}>
          <animateMotion dur="2.6s" repeatCount="indefinite" path="M 210 52 H 106" />
        </rect>
        <rect x="-9" y="-7" width="18" height="14" rx="4" fill={P.bag} stroke={P.bagStroke}>
          <animateMotion dur="2.6s" begin="1.3s" repeatCount="indefinite" path="M 210 52 H 106" />
        </rect>
        {/* грузовик кабиной к выезду */}
        <g transform="translate(16 34)" filter={`url(#${DEFS.soft})`}>
          <rect x="22" y="6" width="66" height="26" rx="4" fill="#eceff3" stroke="#c3cad4" />
          <rect x="26" y="10" width="58" height="18" rx="3" fill="#00000022" />
          <rect x="-4" y="10" width="26" height="22" rx="4" fill="#2f6fdd" />
          <rect x="2" y="13" width="11" height="9" rx="2" fill="#bcd4ff" />
          <circle cx="10" cy="34" r="6" fill="#263238" />
          <circle cx="36" cy="34" r="6" fill="#263238" />
          <circle cx="74" cy="34" r="6" fill="#263238" />
        </g>
        <text x="108" y="128" textAnchor="middle" fontSize="10.5" fill={P.textMuted}>
          {name}
        </text>
      </g>
    </g>
  );
}
