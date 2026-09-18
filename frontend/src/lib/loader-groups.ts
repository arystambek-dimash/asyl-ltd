import { shiftIsoDate, type LoaderOrder } from "@/lib/loader";

/** День, на который заказ ждут: плановая дата, иначе день создания. */
export function plannedDay(order: LoaderOrder): string {
  return order.arrival_date ?? order.created_at.slice(0, 10);
}

export interface LoaderDayGroup {
  day: string;
  /** «ПРОСРОЧЕНО», «СЕГОДНЯ», «ЗАВТРА» или дата — крупная плашка над карточками. */
  label: string;
  /** Дата рядом с плашкой: «11.08». */
  date: string;
  overdue: boolean;
  orders: LoaderOrder[];
}

export function shortDate(iso: string): string {
  const [, month, day] = iso.split("-");
  return `${day}.${month}`;
}

/**
 * Очередь грузчика по дням: сначала просроченные (самые старые сверху),
 * потом сегодня, завтра и дальше. Внутри дня порядок остаётся серверным.
 */
export function groupByPlannedDay(orders: LoaderOrder[], today: string): LoaderDayGroup[] {
  const byDay = new Map<string, LoaderOrder[]>();
  for (const order of orders) {
    const day = plannedDay(order);
    byDay.set(day, [...(byDay.get(day) ?? []), order]);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, group]) => ({
      day,
      label: day < today ? "ПРОСРОЧЕНО" : day === today ? "СЕГОДНЯ" : day === shiftIsoDate(today, 1) ? "ЗАВТРА" : "",
      date: shortDate(day),
      overdue: day < today,
      orders: group,
    }));
}
