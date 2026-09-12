"use client";
import { SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import type { Department, Store } from "@/lib/types";
import { CashFilterFields } from "./cash-filter-fields";
import { activeFilterCount, type CashFilters } from "./filters";

/** Иконка фильтров в топбаре подэкрана; бейдж — сколько групп задано. */
export function FilterButton({ count, onClick }: { count: number; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={count ? `Фильтры, применено: ${count}` : "Фильтры"}
      className="relative flex size-8 items-center justify-center rounded-lg border text-[var(--foreground)]"
    >
      <SlidersHorizontal className="size-4" />
      {count > 0 && (
        <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--foreground)] px-1 text-[10px] font-semibold tabular-nums text-[var(--background)]">
          {count}
        </span>
      )}
    </button>
  );
}

/** Те же поля, что в десктопной панели, но шторкой снизу; изменения применяются сразу. */
export function CashFiltersSheet({
  open,
  onClose,
  filters,
  stores,
  departments,
  showRemaining,
  showDates = true,
  onChange,
}: {
  open: boolean;
  onClose: () => void;
  filters: CashFilters;
  stores: Store[];
  departments: Department[];
  showRemaining: boolean;
  showDates?: boolean;
  onChange: (patch: Partial<CashFilters>) => void;
}) {
  // Отдел на телефоне выбирается в шапке кассы, а не здесь.
  const count = activeFilterCount(filters, { dates: showDates, remaining: showRemaining, department: false });
  // Сброс трогает только группы, которые видны в этой шторке — скрытый период
  // отчёта не должен меняться от «Сбросить» на экране без дат.
  const reset = () =>
    onChange({
      store: "all",
      ...(showDates ? { dateFrom: "", dateTo: "" } : {}),
      ...(showRemaining ? { remainingMin: "", remainingMax: "", remainingCurrency: "all" } : {}),
    });
  return (
    <Modal
      variant="sheet"
      open={open}
      onClose={onClose}
      eyebrow="Касса"
      title="Фильтры"
      description={count ? `Применено: ${count}` : "Без ограничений"}
      footer={
        <>
          <Button variant="ghost" disabled={!count} onClick={reset}>
            <X className="size-4" /> Сбросить
          </Button>
          <Button onClick={onClose}>Готово</Button>
        </>
      }
    >
      <CashFilterFields
        layout="stack"
        filters={filters}
        stores={stores}
        departments={departments}
        showRemaining={showRemaining}
        showDates={showDates}
        showDepartment={false}
        onChange={onChange}
      />
    </Modal>
  );
}
