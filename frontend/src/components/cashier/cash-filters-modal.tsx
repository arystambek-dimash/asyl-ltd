"use client";
import { useState } from "react";
import { SlidersHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import type { Department, Store } from "@/lib/types";
import { CashFilterFields } from "./cash-filter-fields";
import { activeFilterCount, type CashFilters } from "./filters";

/**
 * Фильтры кассы в окне, как аналитика в «Заказах»: на экране остаётся одна
 * кнопка со счётчиком применённых фильтров, а не полоса полей.
 */
export function CashFiltersModal({
  filters,
  stores,
  departments,
  showRemaining,
  onChange,
  onReset,
}: {
  filters: CashFilters;
  stores: Store[];
  departments: Department[];
  showRemaining: boolean;
  onChange: (patch: Partial<CashFilters>) => void;
  onReset: () => void;
}) {
  const activeCount = activeFilterCount(filters, { remaining: showRemaining });
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        <SlidersHorizontal className="size-4" /> Фильтры
        {activeCount > 0 && (
          <span className="ml-1 rounded-full bg-[var(--foreground)] px-1.5 text-xs font-semibold text-[var(--background)] tabular-nums">
            {activeCount}
          </span>
        )}
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Фильтры кассы"
        description={activeCount ? `Применено: ${activeCount}` : "Без ограничений · все оплаты"}
        className="max-w-2xl"
        mobileFullscreen
        footer={
          <>
            {activeCount > 0 && (
              <Button variant="ghost" onClick={onReset}>
                Сбросить
              </Button>
            )}
            <Button onClick={() => setOpen(false)}>Готово</Button>
          </>
        }
      >
        <CashFilterFields
          filters={filters}
          stores={stores}
          departments={departments}
          showRemaining={showRemaining}
          onChange={onChange}
        />
      </Modal>
    </>
  );
}
