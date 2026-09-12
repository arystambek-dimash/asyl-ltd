import { cn } from "@/lib/utils";

export interface DonutSegment {
  key: string;
  label: string;
  value: number;
  /**
   * Любой CSS-цвет; для способов оплаты — токены темы, для отделов — их цвет.
   * Контраст заливки к поверхности карточки — не ниже 3:1 (скилл dataviz,
   * проверка контраста); доли уже озвучены текстом в aria-label, поэтому сам
   * цвет не остаётся единственным носителем различия.
   */
  color: string;
}

/**
 * Кольцевая диаграмма долей без зависимостей: SVG-дуги через pathLength=100,
 * значение и подпись в центре, текстовое описание долей для скринридера.
 * Ориентир скилла dataviz: часть-от-целого в кольце читается только до
 * ~6 сегментов, дальше нагляднее таблица/список.
 */
export function DonutChart({
  segments,
  centerValue,
  centerLabel,
  emptyLabel = "Нет данных",
  size = 208,
  thickness = 22,
  className,
}: {
  segments: DonutSegment[];
  centerValue: string;
  centerLabel?: string;
  emptyLabel?: string;
  size?: number;
  thickness?: number;
  className?: string;
}) {
  const positive = segments.filter((segment) => segment.value > 0);
  const total = positive.reduce((sum, segment) => sum + segment.value, 0);
  const radius = (size - thickness) / 2;
  const center = size / 2;
  let offset = 0;
  const arcs = positive.map((segment) => {
    const share = (segment.value / total) * 100;
    const arc = { ...segment, share, start: offset };
    offset += share;
    return arc;
  });
  const description = total > 0 ? arcs.map((arc) => `${arc.label}: ${Math.round(arc.share)}%`).join(", ") : emptyLabel;

  return (
    <div className={cn("relative mx-auto", className)} style={{ width: size, height: size }}>
      <svg
        role="img"
        aria-label={`${centerValue}${centerLabel ? `, ${centerLabel}` : ""}. ${description}`}
        viewBox={`0 0 ${size} ${size}`}
        className="size-full -rotate-90"
      >
        <circle cx={center} cy={center} r={radius} fill="none" stroke="var(--muted)" strokeWidth={thickness} />
        {arcs.map((arc) => (
          <circle
            key={arc.key}
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke={arc.color}
            strokeWidth={thickness}
            pathLength={100}
            strokeDasharray={`${arc.share} ${100 - arc.share}`}
            strokeDashoffset={-arc.start}
          />
        ))}
      </svg>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-6 text-center">
        <span className="text-[22px] font-bold leading-none tracking-tight tabular-nums">{centerValue}</span>
        {centerLabel && <span className="mt-1.5 text-xs text-[var(--muted-foreground)]">{centerLabel}</span>}
      </div>
    </div>
  );
}
