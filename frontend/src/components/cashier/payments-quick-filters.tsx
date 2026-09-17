"use client";
import { Chip } from "@/components/ui/chip";
import { periodPresetOf, periodRange } from "./filters";
import { ALL_DEPARTMENTS } from "./scope";
import type { CashierModel } from "./use-cashier";

/**
 * Быстрые фильтры «Оплат» на телефоне: сегодня или все даты и отдел.
 * Отдел выбирается чипами только у кассы без закрепления, пока шапка не сузила её до одного отдела.
 */
export function PaymentsQuickFilters({ model }: { model: CashierModel }) {
  const { filters, patchFilters, departments, scope } = model;
  const preset = periodPresetOf(filters);
  const showDepartments = !scope.assigned && scope.department === ALL_DEPARTMENTS && departments.length > 1;

  return (
    <div className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1" role="group" aria-label="Быстрые фильтры">
      <Chip active={preset === "all"} onClick={() => patchFilters(periodRange("all"))}>
        Все даты
      </Chip>
      <Chip active={preset === "today"} onClick={() => patchFilters(periodRange("today"))}>
        Сегодня
      </Chip>
      {showDepartments && (
        <>
          <span aria-hidden className="my-1 w-px shrink-0 bg-[var(--border)]" />
          <Chip
            active={filters.department === ALL_DEPARTMENTS}
            onClick={() => patchFilters({ department: ALL_DEPARTMENTS })}
          >
            Все отделы
          </Chip>
          {departments
            .filter((row) => row.is_active)
            .map((row) => (
              <Chip
                key={row.code}
                active={filters.department === row.code}
                onClick={() => patchFilters({ department: row.code })}
              >
                <span aria-hidden className="size-2 rounded-full" style={{ backgroundColor: row.color }} />
                {row.name}
              </Chip>
            ))}
        </>
      )}
    </div>
  );
}
