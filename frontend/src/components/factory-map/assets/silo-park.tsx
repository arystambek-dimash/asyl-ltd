import type { GrainSilo } from "@/lib/types";
import { DEFS, MAP_PALETTE as P } from "../palette";

/**
 * Цистерны хранения зерна. Уровень заполнения — живой, из /grain/silos/.
 *
 * Один цилиндр рисуется в нативном блоке 64×188 (купол + корпус) с подписями
 * ниже; группа масштабируется под бокс зоны. Чтобы заменить графику на
 * сгенерированную, перерисуйте содержимое <Cylinder> (сохраните clipPath —
 * он обрезает «зерно» по корпусу). Промпты: ../SVG_PROMPTS.md
 */
const CYL_W = 64;
const CYL_GAP = 30;
const BODY_H = 170;
const DOME_H = 18;
const LABELS_H = 52;

function shortName(name: string) {
  // «Силос Первый сорт» → «Первый сорт», но «Силос-1» остаётся «Силос-1».
  const clean = name.replace(/^силос\s+/i, "").trim() || name;
  return clean.length > 10 ? `${clean.slice(0, 9)}…` : clean;
}

function Cylinder({
  clipId,
  silo,
  index,
  waveDur,
}: {
  clipId: string;
  silo: GrainSilo | null;
  index: number;
  waveDur: string;
}) {
  const pct = silo ? Math.max(0, Math.min(100, silo.fill_percent)) / 100 : 0;
  const quarantine = silo?.is_quarantine;
  const inactive = silo ? silo.status !== "active" : false;
  const fillTop = DOME_H + BODY_H * (1 - pct);
  const tons = silo ? Math.round(silo.current_balance_kg / 1000) : null;
  const capTons = silo ? Math.round(silo.total_capacity_kg / 1000) : null;

  return (
    <g opacity={inactive ? 0.62 : 1}>
      <path d={`M 4 ${DOME_H - 12} L 32 ${-0} L 60 ${DOME_H - 12} Z`} fill={P.steelDark} filter={`url(#${DEFS.soft})`} />
      <g transform={`translate(0 ${DOME_H})`}>
        <rect width={CYL_W} height={BODY_H} rx="10" fill={`url(#${DEFS.siloBody})`} stroke={quarantine ? "#dc8181" : P.border} />
        <clipPath id={clipId}>
          <rect width={CYL_W} height={BODY_H} rx="10" />
        </clipPath>
        {silo && pct > 0.01 && (
          <g clipPath={`url(#${clipId})`}>
            <g transform={`translate(0 ${fillTop - DOME_H - 6})`}>
              <path d={`M -34 8 Q -18 0 -2 8 T 30 8 T 62 8 T 94 8 T 126 8 V ${BODY_H + 40} H -34 Z`} fill={`url(#${DEFS.grain})`}>
                <animateTransform attributeName="transform" type="translate" values="-32 0;0 0;-32 0" dur={waveDur} repeatCount="indefinite" />
              </path>
            </g>
          </g>
        )}
        <rect width={CYL_W} height={BODY_H} rx="10" fill="none" stroke={quarantine ? "#dc8181" : P.border} />
      </g>
      <text x="32" y={DOME_H + 26} textAnchor="middle" fontSize="14" fontWeight="700" fill={quarantine ? "#b8463b" : "#5b6472"}>
        {index + 1}
      </text>
      <text x="32" y={DOME_H + BODY_H + 22} textAnchor="middle" fontSize="15" fontWeight="700" fill={P.text} style={{ fontVariantNumeric: "tabular-nums" }}>
        {silo ? `${Math.round(pct * 100)}%` : "—"}
      </text>
      <text x="32" y={DOME_H + BODY_H + 37} textAnchor="middle" fontSize="10" fill={P.textMuted}>
        {silo ? `${tons} т из ${capTons}` : "нет данных"}
      </text>
      <text x="32" y={DOME_H + BODY_H + 50} textAnchor="middle" fontSize="9.5" fill={P.textMuted}>
        {silo ? shortName(silo.name) : ""}
      </text>
    </g>
  );
}

export function SiloParkAsset({
  zoneId,
  width,
  height,
  name,
  silos,
}: {
  zoneId: string;
  width: number;
  height: number;
  name: string;
  silos: GrainSilo[] | null;
}) {
  const rows = silos && silos.length > 0 ? silos : [null, null, null];
  const artWidth = rows.length * CYL_W + (rows.length - 1) * CYL_GAP;
  const artHeight = DOME_H + BODY_H + LABELS_H;
  const scale = Math.min(width / artWidth, (height - 8) / artHeight);
  const offsetX = (width - artWidth * scale) / 2;
  const offsetY = (height - artHeight * scale) / 2 + 4;

  return (
    <g>
      <text x={width / 2} y="-9" textAnchor="middle" fontSize="12" fontWeight="600" fill={P.textMuted}>
        {name}
      </text>
      <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
        {rows.map((silo, index) => (
          <g key={silo?.id ?? `empty-${index}`} transform={`translate(${index * (CYL_W + CYL_GAP)} 0)`}>
            <Cylinder
              clipId={`fm-silo-${zoneId}-${index}`}
              silo={silo}
              index={index}
              waveDur={`${4 + (index % 3) * 0.7}s`}
            />
          </g>
        ))}
      </g>
    </g>
  );
}
