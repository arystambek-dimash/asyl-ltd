import { DEFS, MAP_PALETTE as P } from "../palette";

/**
 * Вагон на ж/д ветке — живой оверлей: появляется, когда поезд на территории
 * (/grain/wagons/?scope=on_site). Нативный холст 200×64.
 * Замена графики: перерисуйте содержимое, сохранив текстовый слот номера.
 * Промпты: ../SVG_PROMPTS.md
 */
export function WagonArt({ number, statusLabel }: { number: string; statusLabel?: string }) {
  const plate = number.length > 12 ? `${number.slice(0, 11)}…` : number;

  return (
    <g filter={`url(#${DEFS.soft})`}>
      <rect width="200" height="42" rx="6" fill="#8d6e63" />
      <rect x="6" y="6" width="188" height="30" rx="4" fill="#a1887f" />
      <rect x="12" y="12" width="176" height="18" rx="3" fill="#00000030" />
      <path d="M 16 24 Q 40 14 66 22 T 120 22 T 172 24 V 30 H 16 Z" fill={`url(#${DEFS.grain})`} opacity=".9" />
      <rect x="108" y="13" width="78" height="16" rx="2" fill="#000000aa" />
      <text x="147" y="25" textAnchor="middle" fontSize="9" fontWeight="700" fill="#ffffff" fontFamily="ui-monospace, monospace">
        № {plate}
      </text>
      <circle cx="40" cy="46" r="7" fill="#37474f" />
      <circle cx="90" cy="46" r="7" fill="#37474f" />
      <circle cx="115" cy="46" r="7" fill="#37474f" />
      <circle cx="165" cy="46" r="7" fill="#37474f" />
      {statusLabel && (
        <g transform="translate(100 60)">
          <rect x="-58" y="0" width="116" height="18" rx="9" fill="#ffffff" stroke={P.border} />
          <text x="0" y="12.5" textAnchor="middle" fontSize="9.5" fontWeight="650" fill={P.text}>
            {statusLabel.length > 22 ? `${statusLabel.slice(0, 21)}…` : statusLabel}
          </text>
        </g>
      )}
    </g>
  );
}
