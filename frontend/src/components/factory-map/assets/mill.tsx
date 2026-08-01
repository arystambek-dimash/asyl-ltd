import { DEFS, MAP_PALETTE as P } from "../palette";

/**
 * Мельница · производственный корпус с роботом KUKA и фасовкой.
 * Нативный холст 300×220, масштабируется под бокс зоны.
 * Замена графики: перерисуйте содержимое <g> внутри scale-группы,
 * сохранив нативный размер (или обновите ART_W/ART_H). Промпты: ../SVG_PROMPTS.md
 */
const ART_W = 300;
const ART_H = 220;

export function MillAsset({ width, height, name, note }: { width: number; height: number; name: string; note: string }) {
  const scale = Math.min(width / ART_W, height / ART_H);
  const offsetX = (width - ART_W * scale) / 2;
  const offsetY = (height - ART_H * scale) / 2;

  return (
    <g>
      <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
        <rect y="20" width="300" height="200" rx="14" fill={`url(#${DEFS.mill})`} stroke={P.border} filter={`url(#${DEFS.soft})`} />
        <rect y="20" width="300" height="26" rx="13" fill="#bfdccb" />
        <text x="150" y="38" textAnchor="middle" fontSize="13" fontWeight="650" fill="#2e5d43">
          {name.length > 34 ? `${name.slice(0, 33)}…` : name}
        </text>
        <g fill="#ffffff" opacity=".75">
          <rect x="150" y="58" width="30" height="20" rx="4" />
          <rect x="188" y="58" width="30" height="20" rx="4" />
          <rect x="226" y="58" width="30" height="20" rx="4" />
        </g>
        {note && (
          <text x="18" y="72" fontSize="10.5" fill={P.textMuted}>
            {note.length > 22 ? `${note.slice(0, 21)}…` : note}
          </text>
        )}
        {/* фасовка: лента подачи мешков */}
        <g transform="translate(14 182)">
          <rect width="84" height="10" rx="5" fill="#39424e" />
          <rect x="3" y="3" width="78" height="4" rx="2" fill="#cdd5de" opacity=".6" />
          <text x="40" y="26" textAnchor="middle" fontSize="10.5" fill={P.textMuted}>
            фасовка
          </text>
        </g>
        <rect x="66" y="166" width="22" height="16" rx="4" fill={P.bag} stroke={P.bagStroke} />
        {/* робот KUKA: двухзвенный манипулятор с медленным циклом укладки */}
        <g transform="translate(170 192)">
          <rect x="-26" width="52" height="10" rx="3" fill="#2b313a" />
          <rect x="-16" y="-22" width="32" height="24" rx="6" fill="#ff8a00" />
          <g transform="translate(0 -24)">
            <g>
              <animateTransform attributeName="transform" type="rotate" values="-146;-210;-146" dur="5.4s" repeatCount="indefinite" calcMode="spline" keySplines=".45 0 .55 1;.45 0 .55 1" />
              <rect x="-8" y="-9" width="62" height="17" rx="8.5" fill="#ff8a00" stroke="#e07000" />
              <circle cx="52" r="7" fill="#3a414b" />
              <g transform="translate(52 0)">
                <g>
                  <animateTransform attributeName="transform" type="rotate" values="118;172;118" dur="5.4s" repeatCount="indefinite" calcMode="spline" keySplines=".45 0 .55 1;.45 0 .55 1" />
                  <rect x="-6" y="-7" width="52" height="13" rx="6.5" fill="#ffa033" stroke="#e07000" />
                  <circle cx="44" r="5.5" fill="#22272e" />
                  <rect x="41" y="3" width="4" height="13" rx="2" fill="#22272e" />
                  <rect x="47" y="3" width="4" height="13" rx="2" fill="#22272e" />
                </g>
              </g>
            </g>
            <circle r="7.5" fill="#3a414b" />
            <text x="-1" y="-12" textAnchor="middle" fontSize="8" fontWeight="800" fill="#7c3f00">
              KUKA
            </text>
          </g>
        </g>
        {/* паллета с мешками */}
        <g transform="translate(206 178)">
          <rect y="22" width="64" height="8" rx="2" fill={P.wood} />
          <rect x="4" y="24" width="10" height="4" fill={P.woodDark} />
          <rect x="27" y="24" width="10" height="4" fill={P.woodDark} />
          <rect x="50" y="24" width="10" height="4" fill={P.woodDark} />
          <rect x="4" y="8" width="20" height="14" rx="4" fill={P.bagAlt} stroke={P.bagStroke} />
          <rect x="26" y="8" width="20" height="14" rx="4" fill={P.bag} stroke={P.bagStroke} />
          <rect x="15" y="-5" width="20" height="14" rx="4" fill={P.bag} stroke={P.bagStroke} />
        </g>
        <text x="150" y="216" textAnchor="middle" fontSize="10.5" fill={P.textMuted}>
          Робот KUKA · укладка на паллету
        </text>
      </g>
    </g>
  );
}
