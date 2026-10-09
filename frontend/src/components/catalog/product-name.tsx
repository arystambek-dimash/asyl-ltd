import type { FilterOption, ProductParts } from "@/lib/product-parts";
import { cn } from "@/lib/utils";

/** Фасовка плашкой: не обрезается, даже когда длинная марка не помещается. */
function WeightChip({ weight, muted }: { weight: number; muted?: boolean }) {
  if (!weight) return null;
  return (
    <span
      className={cn(
        "shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold tabular-nums",
        muted ? "bg-slate-200 text-slate-600" : "bg-slate-900 text-white",
      )}
    >
      {weight} кг
    </span>
  );
}

/**
 * Товар двумя строками: марка — главная (крупно, тёмным, по ней ищут глазами),
 * ниже сорт и фасовка. Цвет — только когда без него два товара не различить.
 * Длинная марка переносится, а не обрезается: у двойников в конце строки цвет.
 */
export function ProductName({
  parts,
  color,
  muted,
  className,
}: {
  parts: ProductParts;
  color?: string;
  muted?: boolean;
  className?: string;
}) {
  return (
    <span className={cn("block min-w-0 flex-1", className)}>
      <span className="block break-words text-sm font-bold uppercase leading-5 tracking-wide text-slate-900">
        {parts.brand}
        {color && <span className="font-medium normal-case text-slate-500"> · {color}</span>}
      </span>
      <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 leading-5">
        {/* Без сорта в названии («Мука 50 кг») — только фасовка: марка уже строкой выше. */}
        {parts.grade && <span className="text-sm text-slate-600">{parts.grade}</span>}
        <WeightChip weight={parts.weight} muted={muted} />
      </span>
    </span>
  );
}

/**
 * Кнопки-фильтры «Все · DIKHAN · KOROL…» (марки, фасовки) — переносятся строками,
 * на телефоне видны все сразу. Одна опция — фильтровать нечего, кнопок нет.
 */
export function FilterChips({
  label,
  allLabel = "Все",
  options,
  total,
  value,
  onChange,
  className,
}: {
  /** Имя группы для читалки: «Марка», «Фасовка». */
  label: string;
  /** Подпись кнопки «без фильтра»: два ряда кнопок рядом — «Все марки» и «Все фасовки». */
  allLabel?: string;
  options: FilterOption[];
  total: number;
  /** Ключ опции; "" — все. */
  value: string;
  onChange: (key: string) => void;
  className?: string;
}) {
  if (options.length < 2) return null;
  return (
    <div role="radiogroup" aria-label={label} className={cn("flex flex-wrap gap-1.5", className)}>
      {[{ key: "", name: allLabel, count: total }, ...options].map(({ key, name, count }) => {
        const active = value === key;
        return (
          <button
            key={key || "all"}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(key)}
            className={cn(
              "shrink-0 rounded-full border px-3 py-1.5 text-sm font-semibold transition",
              active
                ? "border-slate-900 bg-slate-900 text-white"
                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
            )}
          >
            {name}
            <span className={cn("ml-1.5 text-xs font-medium", active ? "text-white/70" : "text-slate-400")}>
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
