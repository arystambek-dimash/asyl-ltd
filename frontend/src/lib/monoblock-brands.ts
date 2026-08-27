export interface BrandMeta {
  label: string;
  bar: string;
  dot: string;
  recognized: boolean;
}

const BRAND_META: Record<string, BrandMeta> = {
  korol: {
    label: "Korol",
    bar: "bg-cyan-500",
    dot: "bg-cyan-500",
    recognized: true,
  },
  dikhan_baba: {
    label: "Дихан Баба",
    bar: "bg-violet-500",
    dot: "bg-violet-500",
    recognized: true,
  },
  unknown: {
    label: "Не распознано",
    bar: "bg-amber-400",
    dot: "bg-amber-400",
    recognized: false,
  },
  unclassified: {
    label: "Нет данных (старые)",
    bar: "bg-slate-300",
    dot: "bg-slate-300",
    recognized: false,
  },
};

const FALLBACK: BrandMeta = {
  label: "",
  bar: "bg-indigo-500",
  dot: "bg-indigo-500",
  recognized: true,
};

export function normalizedBrand(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

function fallbackLabel(value: string): string {
  return value
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

export function brandMeta(brand: string): BrandMeta {
  const normalized = normalizedBrand(brand);
  return (
    BRAND_META[normalized] ?? {
      ...FALLBACK,
      label: fallbackLabel(normalized) || "Не указано",
    }
  );
}

export function isKnownBrand(brand: string): boolean {
  return brandMeta(brand).recognized;
}
