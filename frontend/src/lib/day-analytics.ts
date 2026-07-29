import type { AlwaysOnColorAnalytics, AlwaysOnHistoryPoint } from "@/lib/types";

/** «2026-07-28» → «28.07» для подписи столбика. */
export function shortDay(day: string) {
  const [, month, date] = day.split("-");
  return `${date}.${month}`;
}

/** «2026-07-28» → «28.07.2026» для заголовка выбранного дня. */
export function fullDay(day: string) {
  const [year, month, date] = day.split("-");
  return `${date}.${month}.${year}`;
}

/** Цвета одного дня в том же виде, что общий блок «Цвета продукции».
 *
 * Доля считается от суммы распознанного за день, а не от итога: ручная
 * поправка кассира цвета не имеет, иначе проценты не сходились бы в 100.
 */
export function dayColorBreakdown(point?: AlwaysOnHistoryPoint): AlwaysOnColorAnalytics[] {
  const perColor = point?.model_per_color ?? {};
  const base = Object.values(perColor).reduce((sum, value) => sum + value, 0);
  return Object.entries(perColor)
    .filter(([, total]) => total > 0)
    .map(([color, total]) => ({
      color,
      total,
      percent: base > 0 ? Math.round((total * 1000) / base) / 10 : 0,
    }))
    .sort((a, b) => b.total - a.total);
}
