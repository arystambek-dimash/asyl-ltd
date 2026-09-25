/**
 * Единый источник цветовой палитры продукции для монитора AI 24/7.
 *
 * ВАЖНО: Tailwind v4 сканирует исходники статически, поэтому arbitrary-классы
 * (`bg-[#dc604d]`) обязаны присутствовать в коде как готовые строковые литералы,
 * а не собираться из переменных — иначе они не попадут в сборку.
 */

interface ColorMeta {
  label: string;
  /** Заливка сплошным цветом (бары, точки). */
  bar: string;
  /** Точка-индикатор рядом с названием цвета. */
  dot: string;
}

const COLOR_META: Record<string, ColorMeta> = {
  red: { label: "Красный", bar: "bg-[#dc604d]", dot: "bg-[#dc604d]" },
  blue: { label: "Синий", bar: "bg-[#4169d8]", dot: "bg-[#4169d8]" },
  green: { label: "Зелёный", bar: "bg-[#42a779]", dot: "bg-[#42a779]" },
  white: { label: "Белый", bar: "border border-slate-300 bg-slate-100", dot: "border border-slate-300 bg-white" },
};

const FALLBACK: ColorMeta = { label: "", bar: "bg-slate-500", dot: "bg-slate-500" };

// Цвета, которые модель не определила: такие мешки не привязываются к товару.
const UNDETERMINED_COLORS = new Set(["unclassified", "unknown"]);

export function normalizedColor(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

export function isUndeterminedColor(color: string | null | undefined): boolean {
  return UNDETERMINED_COLORS.has(normalizedColor(color));
}

export function colorMeta(color: string): ColorMeta {
  if (isUndeterminedColor(color)) {
    return { ...FALLBACK, label: "Не определён" };
  }
  return COLOR_META[normalizedColor(color)] ?? { ...FALLBACK, label: color };
}
