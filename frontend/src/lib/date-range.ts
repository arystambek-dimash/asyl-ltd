import { shiftIsoDate, todayLocalIsoDate } from "@/lib/utils";

/** Быстрый выбор периода: неделя и 30 дней — по сегодня включительно, месяц — с 1-го числа, «all» — без границ. */
type PeriodPreset = "today" | "yesterday" | "week" | "last30" | "month" | "all";

/** Период «с — по» датами «ГГГГ-ММ-ДД»; пустая строка — граница не задана. */
interface DateRange {
  dateFrom: string;
  dateTo: string;
}

/** Кнопка пресета на конкретном экране: набор и подписи у экранов свои, границы — общие. */
export interface PeriodOption<P extends PeriodPreset = PeriodPreset> {
  key: P;
  label: string;
}

export function periodRange(preset: PeriodPreset, today = todayLocalIsoDate()): DateRange {
  switch (preset) {
    case "today":
      return { dateFrom: today, dateTo: today };
    case "yesterday": {
      const yesterday = shiftIsoDate(today, -1);
      return { dateFrom: yesterday, dateTo: yesterday };
    }
    case "week":
      return { dateFrom: shiftIsoDate(today, -6), dateTo: today };
    case "last30":
      return { dateFrom: shiftIsoDate(today, -29), dateTo: today };
    case "month":
      return { dateFrom: `${today.slice(0, 8)}01`, dateTo: today };
    case "all":
      return { dateFrom: "", dateTo: "" };
  }
}

/** Какой из пресетов экрана совпадает с периодом; «custom» — даты выбраны вручную. */
export function periodPresetOf<P extends PeriodPreset>(
  range: DateRange,
  options: readonly PeriodOption<P>[],
  today = todayLocalIsoDate(),
): P | "custom" {
  const match = options.find(({ key }) => {
    const preset = periodRange(key, today);
    return preset.dateFrom === range.dateFrom && preset.dateTo === range.dateTo;
  });
  return match?.key ?? "custom";
}
