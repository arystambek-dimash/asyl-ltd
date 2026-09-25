"use client";
import { Chip } from "@/components/ui/chip";
import { DepartmentDot } from "@/components/ui/department-badge";
import { periodPresetOf, periodRange } from "@/lib/date-range";
import { PERIOD_PRESETS } from "./filters";
import { ALL_DEPARTMENTS } from "./scope";
import type { CashierModel } from "./use-cashier";

/**
 * Быстрые фильтры «Оплат» на телефоне: сегодня или все даты и отдел.
 * Отдел выбирается чипами только у кассы без закрепления, пока шапка не сузила её до одного отдела.
 */
export function PaymentsQuickFilters({ model }: { model: CashierModel }) {
  const { filters, patchFilters, departments, scope } = model;
  const preset = periodPresetOf(filters, PERIOD_PRESETS);
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
          {departments.map((row) => (
            <Chip
              key={row.code}
              active={filters.department === row.code}
              onClick={() => patchFilters({ department: row.code })}
            >
              <DepartmentDot color={row.color} className="size-2" />
              {row.name}
            </Chip>
          ))}
        </>
      )}
    </div>
  );
}
