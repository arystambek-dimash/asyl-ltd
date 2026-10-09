/**
 * Название товара по частям: марка, сорт и фасовка — без цвета.
 *
 * В каталоге нет отдельного поля марки: названия пишут как «Первый сорт
 * DIKHAN BABA NAN 50кг», сорт бывает и после марки («KOROL высший сорт 50кг»).
 * Один разбор — для выбора товара в заказе и для списка склада.
 */
import { lookalikeKey } from "@/lib/plates";

const GRADE = /(высший|первый|второй|третий)\s+сорт/i;
const GRADE_RANK = ["высший", "первый", "второй", "третий"];
const WEIGHT_SUFFIX = /\s*\d+(?:[.,]\d+)?\s*(?:кг|kg)\.?\s*$/i;

export interface ProductParts {
  brand: string;
  grade: string;
  weight: number;
}

export function productParts(name: string, weightKg: string | number | null | undefined): ProductParts {
  const clean = name.replace(/\s+/g, " ").trim();
  const grade = clean.match(GRADE)?.[1]?.toLocaleLowerCase("ru") ?? "";
  const brand = clean.replace(GRADE, " ").replace(WEIGHT_SUFFIX, "").replace(/\s+/g, " ").trim() || clean;
  return {
    brand,
    grade: grade ? `${grade[0].toLocaleUpperCase("ru")}${grade.slice(1)} сорт` : "",
    weight: Number(weightKg) || 0,
  };
}

/** Ключ марки: регистр не делает из одной марки две. */
export function brandKey(brand: string): string {
  return brand.toLocaleUpperCase("ru");
}

/** Ключ «марка + сорт + фасовка»: совпал у двух товаров — их различает только цвет. */
export function twinKey(parts: ProductParts): string {
  return `${brandKey(parts.brand)}|${parts.grade.toLocaleLowerCase("ru")}|${parts.weight}`;
}

/** Ключи «марка + сорт + фасовка», которые встречаются у нескольких товаров. */
export function twinKeys(parts: ProductParts[]): Set<string> {
  const counts = new Map<string, number>();
  for (const item of parts) counts.set(twinKey(item), (counts.get(twinKey(item)) ?? 0) + 1);
  return new Set([...counts].filter(([, count]) => count > 1).map(([key]) => key));
}

/** «Высший сорт · 50 кг» — сорт и фасовка без марки. */
function gradeLine(parts: ProductParts): string {
  return [parts.grade, parts.weight ? `${parts.weight} кг` : ""].filter(Boolean).join(" · ");
}

/** Товар одной строкой: «KOROL · Высший сорт · 50 кг» (+ цвет у двойников). */
export function productTitle(parts: ProductParts, color?: string): string {
  return [parts.brand, gradeLine(parts), color].filter(Boolean).join(" · ");
}

/**
 * Поиск товара: каждое слово запроса — где угодно («первый 25», «korol 50»),
 * кириллические двойники — как латиница: «Д1с» найдёт и код «Д1c».
 */
export function productSearch(query: string): (texts: (string | undefined)[]) => boolean {
  const words = lookalikeKey(query).split(/\s+/).filter(Boolean);
  return (texts) => {
    if (!words.length) return true;
    const haystack = lookalikeKey(texts.filter(Boolean).join(" "));
    return words.every((word) => haystack.includes(word));
  };
}

export interface FilterOption {
  key: string;
  name: string;
  count: number;
}

/** Марки в постоянном порядке: у кого больше товаров — первыми, равные — по алфавиту. */
export function productBrands(parts: ProductParts[]): FilterOption[] {
  const byKey = new Map<string, FilterOption>();
  for (const { brand } of parts) {
    const key = brandKey(brand);
    const found = byKey.get(key);
    byKey.set(key, { key, name: found?.name ?? brand, count: (found?.count ?? 0) + 1 });
  }
  return [...byKey.values()].sort((a, b) => b.count - a.count || a.key.localeCompare(b.key, "ru"));
}

/** Фасовки кнопками: крупная — первой. */
export function productWeights(parts: ProductParts[]): FilterOption[] {
  const counts = new Map<number, number>();
  for (const { weight } of parts) if (weight) counts.set(weight, (counts.get(weight) ?? 0) + 1);
  return [...counts]
    .sort(([a], [b]) => b - a)
    .map(([weight, count]) => ({ key: String(weight), name: `${weight} кг`, count }));
}

function gradeRank(grade: string) {
  const rank = GRADE_RANK.indexOf(grade.split(" ")[0].toLocaleLowerCase("ru"));
  return rank < 0 ? GRADE_RANK.length : rank;
}

/** Порядок товаров: по марке (как в `brands`), затем высший → первый → второй, крупная фасовка выше. */
export function compareProductParts(a: ProductParts, b: ProductParts, brands: FilterOption[]): number {
  const order = (parts: ProductParts) => brands.findIndex(({ key }) => key === brandKey(parts.brand));
  return order(a) - order(b) || gradeRank(a.grade) - gradeRank(b.grade) || b.weight - a.weight;
}
