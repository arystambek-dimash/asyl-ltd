import { can } from "@/lib/can";
import { shiftIsoDate, type LoaderOrder } from "@/lib/loader";
import type { Me } from "@/lib/types";

export type LoaderTransport = LoaderOrder["transport_type"];

/** Вкладки «Фуры | Вагоны»: область грузчика — отдельное право (как в perms.py). */
export const LOADER_TRANSPORTS: { key: LoaderTransport; label: string; perm: string }[] = [
  { key: "truck", label: "Фуры", perm: "loader.trucks" },
  { key: "train", label: "Вагоны", perm: "loader.wagons" },
];

/** Области, открытые грузчику; суперпользователю — все. */
export function loaderTransports(me: Me | null): LoaderTransport[] {
  return LOADER_TRANSPORTS.filter((tab) => can(me, tab.perm)).map((tab) => tab.key);
}

/** Вкладка при открытии страницы: последняя выбранная, пока она открыта, иначе первая. */
export function initialLoaderTransport(allowed: LoaderTransport[], stored: string | null): LoaderTransport | null {
  return allowed.find((key) => key === stored) ?? allowed[0] ?? null;
}

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

/** Фильтр очереди — как уходит в `?day=&overdue=1&search=`; пустое — без условия. */
export interface LoaderQueueFilter {
  day: string;
  overdue: string;
  search: string;
}

/**
 * Подходит ли заказ под показанную очередь — тем же правилом, что сервер:
 * плановый день и «Просрочено». Поиск сверяет только сервер — тогда `false`,
 * строку вернёт тихое обновление.
 */
export function inQueueFilter(order: LoaderOrder, filter: LoaderQueueFilter, today: string): boolean {
  if (filter.search) return false;
  const day = plannedDay(order);
  return (!filter.overdue || day < today) && (!filter.day || day === filter.day);
}

/**
 * Вернуть заказ в показанную очередь (ответ отмены отгрузки) в серверном
 * порядке: плановый день, затем номер. Строку с тем же номером заменяет.
 */
export function withQueueRow(rows: LoaderOrder[], row: LoaderOrder): LoaderOrder[] {
  const key = (order: LoaderOrder) => [plannedDay(order), order.id] as const;
  const [day, id] = key(row);
  const rest = rows.filter((order) => order.id !== id);
  const index = rest.findIndex((order) => {
    const [otherDay, otherId] = key(order);
    return otherDay > day || (otherDay === day && otherId > id);
  });
  return index === -1 ? [...rest, row] : [...rest.slice(0, index), row, ...rest.slice(index)];
}

/** День выезда отгрузки — как его отдал сервер (местное время). */
export function shippedDay(order: LoaderOrder, today: string): string {
  return order.shipped_at ? order.shipped_at.slice(0, 10) : today;
}

/**
 * Подходит ли отгрузка под показанную историю: день выезда в диапазоне.
 * Поиск сверяет только сервер — тогда `false`, строку вернёт перечитывание.
 */
export function inHistoryRange(
  order: LoaderOrder,
  range: { from: string; to: string },
  search: string,
  today: string,
): boolean {
  if (search) return false;
  const day = shippedDay(order, today);
  return day >= range.from && day <= range.to;
}

/**
 * Добавить отгрузку в показанную историю (ответ «Провести» по отчёту) в
 * серверном порядке: сначала поздние выезды, затем больший номер. Отчёт за
 * прошедший день встаёт на свой день, а не наверх.
 */
export function withHistoryRow(rows: LoaderOrder[], row: LoaderOrder): LoaderOrder[] {
  const key = (order: LoaderOrder) => [order.shipped_at ?? "", order.id] as const;
  const [shippedAt, id] = key(row);
  const rest = rows.filter((order) => order.id !== id);
  const index = rest.findIndex((order) => {
    const [otherAt, otherId] = key(order);
    return otherAt < shippedAt || (otherAt === shippedAt && otherId < id);
  });
  return index === -1 ? [...rest, row] : [...rest.slice(0, index), row, ...rest.slice(index)];
}
