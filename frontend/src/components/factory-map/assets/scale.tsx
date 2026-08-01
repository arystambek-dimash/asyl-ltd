import { DEFS, MAP_PALETTE as P } from "../palette";

/**
 * Автомобильные весы: платформа и табло. Нативный холст 330×132.
 * Живого веса на карте нет — табло показывает «––––» (реальные значения
 * остаются в посте погрузки). Замена графики: перерисуйте scale-группу.
 * Промпты: ../SVG_PROMPTS.md
 */
const ART_W = 330;
const ART_H = 132;

export function ScaleAsset({
  width,
  height,
  name,
  note,
}: {
  width: number;
  height: number;
  name: string;
  note: string;
}) {
  const scale = Math.min(width / ART_W, height / ART_H);
  const offsetX = (width - ART_W * scale) / 2;
  const offsetY = (height - ART_H * scale) / 2;

  return (
    <g>
      <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
        <rect y="22" width="200" height="70" rx="8" fill={P.asphaltDark} />
        <rect x="8" y="30" width="184" height="54" rx="5" fill="#90a4ae" />
        <line x1="8" y1="57" x2="192" y2="57" stroke="#ffffff" strokeOpacity=".45" strokeWidth="2" />
        <g transform="translate(216 36)" filter={`url(#${DEFS.soft})`}>
          <rect width="96" height="40" rx="8" fill="#101418" />
          <text
            x="48"
            y="26"
            textAnchor="middle"
            fontSize="17"
            fontWeight="700"
            fill="#5df08d"
            fontFamily="ui-monospace, monospace"
          >
            ––––
          </text>
        </g>
        <text x="100" y="112" textAnchor="middle" fontSize="10.5" fill={P.textMuted}>
          {name}
          {note ? ` · ${note}` : ""}
        </text>
      </g>
    </g>
  );
}
