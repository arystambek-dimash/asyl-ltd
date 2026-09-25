import { normalizedColor } from "@/lib/monoblock-colors";

const BRAND_LABELS: Record<string, string> = {
  korol: "Korol",
  dikhan_baba: "Дихан Баба",
  unknown: "Не распознано",
  unclassified: "Нет данных (старые)",
};

function fallbackLabel(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

export function brandLabel(brand: string): string {
  const normalized = normalizedColor(brand);
  return BRAND_LABELS[normalized] ?? (fallbackLabel(normalized) || "Не указано");
}
