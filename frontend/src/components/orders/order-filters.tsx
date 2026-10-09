"use client";
import { useRef, useState } from "react";
import { CalendarDays } from "lucide-react";
import { Button } from "@/components/ui/button";
import { FilterDropdown, FilterTrigger } from "@/components/ui/filter-dropdown";
import { Input } from "@/components/ui/input";
import { departmentScope } from "@/components/cashier/scope";
import { useDismiss } from "@/lib/use-dismiss";
import { formatIsoDate } from "@/lib/utils";
import type { Department } from "@/lib/types";
import { useAuth } from "@/store/auth";

/* Фильтры списков «Заказов» — общие для вкладок «Все заказы» и «Возвраты». */

/** «Дата: с — по»: обе даты входят в период (параметры date_from / date_to). */
export function DateRangeFilter({
  dateFrom,
  dateTo,
  onDateFrom,
  onDateTo,
  title = "Дата создания",
}: {
  dateFrom: string;
  dateTo: string;
  onDateFrom: (value: string) => void;
  onDateTo: (value: string) => void;
  /** Заголовок окна: какую дату выбирают. */
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const active = Boolean(dateFrom || dateTo);
  const value =
    dateFrom && dateTo
      ? `${formatIsoDate(dateFrom)} — ${formatIsoDate(dateTo)}`
      : dateFrom
        ? `с ${formatIsoDate(dateFrom)}`
        : dateTo
          ? `по ${formatIsoDate(dateTo)}`
          : "Все";

  useDismiss(ref, () => setOpen(false), open);

  return (
    <div ref={ref} className="relative shrink-0">
      <FilterTrigger
        label="Дата"
        value={value}
        icon={CalendarDays}
        active={active}
        open={open}
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="dialog"
      />

      {open && (
        <div
          role="dialog"
          aria-label={title}
          className="absolute left-0 z-40 mt-1 w-[min(300px,calc(100vw-2rem))] rounded-xl border bg-[var(--card)] p-3 shadow-xl md:left-auto md:right-0"
        >
          <div className="mb-3">
            <div className="text-sm font-semibold">{title}</div>
            <div className="text-xs text-[var(--muted-foreground)]">Обе даты входят в выбранный период.</div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className="text-xs text-[var(--muted-foreground)]">
              С
              <Input
                type="date"
                value={dateFrom}
                max={dateTo || undefined}
                onChange={(event) => onDateFrom(event.target.value)}
                className="mt-1 h-9 px-2.5 text-xs"
              />
            </label>
            <label className="text-xs text-[var(--muted-foreground)]">
              По
              <Input
                type="date"
                value={dateTo}
                min={dateFrom || undefined}
                onChange={(event) => onDateTo(event.target.value)}
                className="mt-1 h-9 px-2.5 text-xs"
              />
            </label>
          </div>
          <div className="mt-3 flex items-center justify-between border-t pt-3">
            <button
              type="button"
              disabled={!active}
              onClick={() => {
                onDateFrom("");
                onDateTo("");
              }}
              className="text-xs font-medium text-[var(--muted-foreground)] transition-colors hover:text-[var(--foreground)] disabled:opacity-40"
            >
              Сбросить
            </button>
            <Button type="button" size="sm" onClick={() => setOpen(false)}>
              Готово
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * «Отдел: …» (параметр department, «__unassigned» — без отдела). Сотруднику,
 * закреплённому за отделом, фильтр не показывается: сервер сам отдаёт только его отдел.
 */
export function DepartmentFilter({
  departments,
  active,
  onChange,
}: {
  departments: Department[] | null | undefined;
  active: string;
  onChange: (code: string) => void;
}) {
  const { me } = useAuth();
  if (!departments?.length || departmentScope(me).assigned) return null;
  return (
    <FilterDropdown
      label="Отдел"
      active={active}
      onChange={onChange}
      options={[
        { key: "all", label: "Все" },
        { key: "__unassigned", label: "Нет отдела" },
        ...departments.map((department) => ({ key: department.code, label: department.name })),
      ]}
    />
  );
}
