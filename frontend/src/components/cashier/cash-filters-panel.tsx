"use client";
import { SlidersHorizontal, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { Department, Store } from "@/lib/types";
import { CashFilterFields } from "./cash-filter-fields";
import { activeFilterCount, type CashFilters } from "./filters";

export function CashFiltersPanel({
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
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex flex-col gap-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span className="flex size-8 items-center justify-center rounded-lg bg-[var(--muted)] text-[var(--muted-foreground)]">
                <SlidersHorizontal className="size-4" />
              </span>
              <div>
                <div className="text-sm font-semibold">Фильтры кассы</div>
                <div className="text-xs text-[var(--muted-foreground)]">
                  {activeCount ? `Применено: ${activeCount}` : "Без ограничений · все оплаты"}
                </div>
              </div>
            </div>
            {activeCount > 0 && (
              <Button size="sm" variant="ghost" onClick={onReset}>
                <X className="size-4" /> Сбросить
              </Button>
            )}
          </div>
          <CashFilterFields
            filters={filters}
            stores={stores}
            departments={departments}
            showRemaining={showRemaining}
            onChange={onChange}
          />
        </div>
      </CardContent>
    </Card>
  );
}
