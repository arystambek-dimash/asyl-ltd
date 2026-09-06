import { toLocalIsoDate } from "@/lib/utils";

const DAY_LABEL_FORMATTER = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

/** Ключ и подпись группы без даты: такие строки всегда идут последними. */
export const UNDATED_DAY_KEY = "undated";
export const UNDATED_DAY_LABEL = "Без даты";

export interface DayGroup<T> {
  /** Локальный календарный день «ГГГГ-ММ-ДД» либо UNDATED_DAY_KEY. */
  key: string;
  label: string;
  items: T[];
}

/** «Сегодня», «Вчера» или «6 сентября 2026» относительно текущего локального дня. */
export function formatDayLabel(date: Date, currentDay: string): string {
  const today = new Date(`${currentDay}T12:00:00`);
  const startOf = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
  const diffDays = Math.round((startOf(today) - startOf(date)) / 86_400_000);
  if (diffDays === 0) return "Сегодня";
  if (diffDays === 1) return "Вчера";
  return DAY_LABEL_FORMATTER.format(date);
}

/**
 * Группирует ленту по календарному дню, сохраняя порядок элементов.
 *
 * Группа — непрерывный отрезок ленты с одним днём, поэтому список должен быть
 * уже отсортирован по дате: иначе один и тот же день появится дважды.
 * Элементы без даты собираются в последнюю группу «Без даты».
 */
export function groupByDay<T>(
  items: readonly T[],
  dateOf: (item: T) => Date | null | undefined,
  currentDay: string,
): DayGroup<T>[] {
  const groups: DayGroup<T>[] = [];
  const undated: T[] = [];
  for (const item of items) {
    const date = dateOf(item);
    if (!date || Number.isNaN(date.getTime())) {
      undated.push(item);
      continue;
    }
    const key = toLocalIsoDate(date);
    let group = groups[groups.length - 1];
    if (!group || group.key !== key) {
      group = { key, label: formatDayLabel(date, currentDay), items: [] };
      groups.push(group);
    }
    group.items.push(item);
  }
  if (undated.length) groups.push({ key: UNDATED_DAY_KEY, label: UNDATED_DAY_LABEL, items: undated });
  return groups;
}
