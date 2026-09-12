"use client";
import { FilterDropdown, type FilterOption } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { Department, Store } from "@/lib/types";
import { cn } from "@/lib/utils";
import { filtersError, type CashFilters } from "./filters";

const CURRENCY_OPTIONS: FilterOption[] = [
  { key: "all", label: "Основная" },
  { key: "KZT", label: "KZT" },
  { key: "USD", label: "USD" },
];

/** Поля фильтров кассы. `row` — десктопная панель, `stack` — мобильная шторка (нативные select открывают системный пикер). */
export function CashFilterFields({
  filters,
  stores,
  departments,
  showRemaining,
  showDates = true,
  layout = "row",
  onChange,
}: {
  filters: CashFilters;
  stores: Store[];
  departments: Department[];
  /** Диапазон остатка долга уместен только там, где есть долги. */
  showRemaining: boolean;
  /** Отчёт на телефоне выбирает период чипами — даты в шторке не нужны. */
  showDates?: boolean;
  layout?: "row" | "stack";
  onChange: (patch: Partial<CashFilters>) => void;
}) {
  const stack = layout === "stack";
  const departmentOptions: FilterOption[] = [
    { key: "all", label: "Все" },
    ...departments.map((department) => ({ key: department.code, label: department.name })),
  ];
  const storeOptions: FilterOption[] = [
    { key: "all", label: "Все" },
    ...[...stores]
      .sort((a, b) => a.name.localeCompare(b.name, "ru"))
      .map((store) => ({ key: String(store.id), label: store.name })),
  ];
  // Остаток долга не показан на этом экране — не считаем его ошибкой, даже если поля где-то заполнены.
  const errorText = filtersError(showRemaining ? filters : { ...filters, remainingMin: "", remainingMax: "" });
  const labelClass = "text-[11px] font-medium text-[var(--muted-foreground)]";

  function choice(label: string, active: string, options: FilterOption[], pick: (key: string) => void) {
    if (!stack) return <FilterDropdown label={label} active={active} onChange={pick} options={options} />;
    return (
      <label className="flex flex-col gap-1.5">
        <span className={labelClass}>{label}</span>
        <Select value={active} onChange={(event) => pick(event.target.value)}>
          {options.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </Select>
      </label>
    );
  }

  return (
    <div className={cn("flex gap-3", stack ? "flex-col" : "flex-wrap items-end")}>
      {showDates && (
        <div className={cn(stack ? "grid grid-cols-2 gap-3" : "contents")}>
          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>С даты</span>
            <Input
              type="date"
              value={filters.dateFrom}
              onChange={(e) => onChange({ dateFrom: e.target.value })}
              className={stack ? "h-10" : "h-9 w-[158px]"}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className={labelClass}>По дату</span>
            <Input
              type="date"
              value={filters.dateTo}
              onChange={(e) => onChange({ dateTo: e.target.value })}
              className={stack ? "h-10" : "h-9 w-[158px]"}
            />
          </label>
        </div>
      )}
      {choice("Отдел", filters.department, departmentOptions, (department) => onChange({ department }))}
      {choice("Магазин", filters.store, storeOptions, (store) => onChange({ store }))}
      {showRemaining && (
        <div className="flex flex-col gap-1.5">
          <span className={labelClass}>Остаток долга</span>
          <div className={cn("flex items-center gap-1.5", stack && "flex-wrap")}>
            {stack ? (
              <Select
                aria-label="Валюта остатка"
                className="w-auto"
                value={filters.remainingCurrency}
                onChange={(event) => onChange({ remainingCurrency: event.target.value })}
              >
                {CURRENCY_OPTIONS.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </Select>
            ) : (
              <FilterDropdown
                label="Валюта"
                active={filters.remainingCurrency}
                onChange={(remainingCurrency) => onChange({ remainingCurrency })}
                options={CURRENCY_OPTIONS}
              />
            )}
            <Input
              type="number"
              min="0"
              inputMode="decimal"
              placeholder="От"
              value={filters.remainingMin}
              onChange={(e) => onChange({ remainingMin: e.target.value })}
              className={stack ? "h-10 flex-1" : "h-9 w-[118px]"}
            />
            <span className="text-[var(--muted-foreground)]">—</span>
            <Input
              type="number"
              min="0"
              inputMode="decimal"
              placeholder="До"
              value={filters.remainingMax}
              onChange={(e) => onChange({ remainingMax: e.target.value })}
              className={stack ? "h-10 flex-1" : "h-9 w-[118px]"}
            />
          </div>
        </div>
      )}
      {errorText && <p className="w-full text-xs font-medium text-[var(--destructive)]">{errorText}</p>}
    </div>
  );
}
