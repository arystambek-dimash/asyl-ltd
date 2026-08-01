import type { StockItem } from "@/lib/types";
import { DEFS, MAP_PALETTE as P } from "../palette";

/**
 * Склад готовой продукции: до четырёх секций с живыми остатками из /stock/.
 * Количество нарисованных мешков в секции пропорционально остатку
 * относительно самой полной секции. Нативный холст 360×440.
 * Замена графики: перерисуйте <Section> при том же контракте. Промпты: ../SVG_PROMPTS.md
 */
const ART_W = 360;
const ART_H = 440;
const SECTION_W = 180;
const SECTION_H = 220;

function formatBags(bags: number) {
  return `${bags.toLocaleString("ru-RU")} меш.`;
}

function Section({ item, ratio }: { item: StockItem | null; ratio: number }) {
  const total = 20;
  const filled = item ? Math.max(item.bags > 0 ? 1 : 0, Math.round(ratio * total)) : 0;
  const label = item ? `${item.grade} · ${item.packaging}` : "Свободная секция";

  return (
    <g>
      <text x={SECTION_W / 2} y="26" textAnchor="middle" fontSize="12.5" fontWeight="600" fill={P.textMuted}>
        {label.length > 24 ? `${label.slice(0, 23)}…` : label}
      </text>
      <g transform="translate(24 44)">
        {Array.from({ length: total }).map((_, index) => {
          const col = index % 5;
          const row = Math.floor(index / 5);
          const isFilled = index < filled;
          return (
            <rect
              key={index}
              x={col * 27}
              y={(3 - row) * 26}
              width="22"
              height="18"
              rx="5"
              fill={isFilled ? (index % 2 ? P.bag : P.bagAlt) : "none"}
              stroke={isFilled ? P.bagStroke : P.border}
              strokeDasharray={isFilled ? undefined : "3 3"}
            />
          );
        })}
      </g>
      <text
        x={SECTION_W / 2}
        y="200"
        textAnchor="middle"
        fontSize="15"
        fontWeight="700"
        fill={item && item.bags < 0 ? "#b8463b" : P.text}
        style={{ fontVariantNumeric: "tabular-nums" }}
      >
        {item ? formatBags(item.bags) : "—"}
      </text>
    </g>
  );
}

export function WarehouseAsset({
  width,
  height,
  name,
  stock,
}: {
  width: number;
  height: number;
  name: string;
  stock: StockItem[] | null;
}) {
  const items = [...(stock ?? [])].sort((a, b) => b.bags - a.bags).slice(0, 4);
  const maxBags = Math.max(1, ...items.map((item) => item.bags));
  const scale = Math.min(width / ART_W, height / ART_H);
  const offsetX = (width - ART_W * scale) / 2;
  const offsetY = (height - ART_H * scale) / 2;

  return (
    <g>
      <text x={width / 2} y="-9" textAnchor="middle" fontSize="12" fontWeight="600" fill={P.textMuted}>
        {name}
      </text>
      <g transform={`translate(${offsetX} ${offsetY}) scale(${scale})`}>
        <rect
          width={ART_W}
          height={ART_H}
          rx="16"
          fill={`url(#${DEFS.roof})`}
          stroke={P.border}
          filter={`url(#${DEFS.soft})`}
        />
        <line x1={SECTION_W} y1="10" x2={SECTION_W} y2={ART_H - 10} stroke={P.border} strokeWidth="2" />
        <line x1="10" y1={SECTION_H} x2={ART_W - 10} y2={SECTION_H} stroke={P.border} strokeWidth="2" />
        {[0, 1, 2, 3].map((index) => {
          const item = items[index] ?? null;
          return (
            <g
              key={item?.id ?? `empty-${index}`}
              transform={`translate(${(index % 2) * SECTION_W} ${Math.floor(index / 2) * SECTION_H})`}
            >
              <Section item={item} ratio={item ? item.bags / maxBags : 0} />
            </g>
          );
        })}
      </g>
    </g>
  );
}
