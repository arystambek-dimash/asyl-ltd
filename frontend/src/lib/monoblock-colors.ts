/**
 * Единый источник цветовой палитры продукции для монитора AI 24/7.
 *
 * Раньше `COLOR_META` дублировался в `app/monoblock/page.tsx` и в
 * `components/monoblock/always-on-production-panel.tsx` с чуть разными
 * оттенками — из-за этого один и тот же «Красный» выглядел по-разному на
 * графике и в списке периодов. Теперь оттенки заданы здесь один раз.
 *
 * ВАЖНО: Tailwind v4 сканирует исходники статически, поэтому arbitrary-классы
 * (`bg-[#dc604d]`) обязаны присутствовать в коде как готовые строковые литералы,
 * а не собираться из переменных — иначе они не попадут в сборку.
 */

export interface ColorMeta {
  label: string;
  /** Заливка сплошным цветом (бары, точки). */
  bar: string;
  /** Точка-индикатор рядом с названием цвета. */
  dot: string;
  /** Мягкая подложка карточки периода (border + tint). */
  tint: string;
  /** Сплошная линия/полоса того же цвета. */
  line: string;
  /** Голый hex — для inline-стилей (например, свечения). */
  hex: string;
}

export const COLOR_META: Record<string, ColorMeta> = {
  red: {
    label: "Красный",
    bar: "bg-[#dc604d]",
    dot: "bg-[#dc604d]",
    tint: "border-[#dc604d]/25 bg-[#dc604d]/[0.07]",
    line: "bg-[#dc604d]",
    hex: "#dc604d",
  },
  blue: {
    label: "Синий",
    bar: "bg-[#4169d8]",
    dot: "bg-[#4169d8]",
    tint: "border-[#4169d8]/25 bg-[#4169d8]/[0.07]",
    line: "bg-[#4169d8]",
    hex: "#4169d8",
  },
  green: {
    label: "Зелёный",
    bar: "bg-[#42a779]",
    dot: "bg-[#42a779]",
    tint: "border-[#42a779]/25 bg-[#42a779]/[0.07]",
    line: "bg-[#42a779]",
    hex: "#42a779",
  },
  white: {
    label: "Белый",
    bar: "border border-slate-300 bg-slate-100",
    dot: "border border-slate-300 bg-white",
    tint: "border-slate-200 bg-slate-50",
    line: "border border-slate-300 bg-white",
    hex: "#ffffff",
  },
};

const FALLBACK: ColorMeta = {
  label: "",
  bar: "bg-slate-500",
  dot: "bg-slate-500",
  tint: "border-slate-200 bg-slate-50",
  line: "bg-slate-500",
  hex: "#64748b",
};

export function normalizedColor(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

export function colorMeta(color: string): ColorMeta {
  return COLOR_META[normalizedColor(color)] ?? { ...FALLBACK, label: color };
}
