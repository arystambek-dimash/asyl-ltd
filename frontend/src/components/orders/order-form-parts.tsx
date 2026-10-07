"use client";
import { Check, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Client, Department, Product, Store, Warehouse } from "@/lib/types";
import { cn } from "@/lib/utils";

export type OrderClientOption = Pick<Client, "id" | "name" | "company_name" | "phone" | "currency"> & {
  /** Страна клиента — страна номера машины по умолчанию. */
  country?: string;
  department_code?: string;
  department_name?: string;
};
export type OrderProductOption = Pick<Product, "id" | "label" | "name" | "weight_kg" | "available_bags"> & {
  /** Коды отчётов («Д1с») — поиск в выборе товара. */
  codes: string[];
  /** Цвет — только чтобы различить два товара с одинаковой подписью. */
  color_label: string;
  /** Остаток мешков по складам: ключ — id склада; склада без записи у товара нет. */
  stock_by_warehouse: Record<string, number>;
};
type OrderStoreOption = Pick<Store, "id" | "client" | "name" | "address">;
type OrderDepartmentOption = Pick<Department, "id" | "code" | "name" | "color" | "is_default">;

/** GET /orders/form-options/ — справочники «Нового заказа» и «Возврата». */
export interface OrderFormOptions {
  clients: OrderClientOption[];
  products: OrderProductOption[];
  stores: OrderStoreOption[];
  departments: OrderDepartmentOption[];
  warehouses?: Warehouse[];
}

export const EMPTY_FORM_OPTIONS: OrderFormOptions = {
  clients: [],
  products: [],
  stores: [],
  departments: [],
  warehouses: [],
};

export function SectionTitle({
  step,
  title,
  caption,
  aside,
}: {
  step: number;
  title: string;
  caption?: string;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex items-center gap-3">
        <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white">
          {step}
        </span>
        <div>
          <h3 className="text-base font-semibold text-slate-900">{title}</h3>
          {caption && <p className="text-xs text-slate-500">{caption}</p>}
        </div>
      </div>
      {aside}
    </div>
  );
}

/**
 * Шаг «Клиент»: выбранный клиент карточкой с «Изменить» или поиск по списку.
 * Один для «Нового заказа» и «Возврата».
 */
export function ClientPicker({
  clients,
  value,
  search,
  onSearch,
  listOpen,
  onOpenList,
  onChoose,
  badge,
  canChange = true,
}: {
  clients: OrderClientOption[];
  value: string;
  search: string;
  onSearch: (value: string) => void;
  /** Поиск со списком вместо карточки выбранного. */
  listOpen: boolean;
  onOpenList: () => void;
  onChoose: (client: OrderClientOption) => void;
  /** Справа в карточке выбранного — например, отдел. */
  badge?: React.ReactNode;
  canChange?: boolean;
}) {
  const selected = clients.find((item) => String(item.id) === value);
  const needle = search.trim().toLocaleLowerCase("ru");
  const filtered = needle
    ? clients.filter((item) =>
        `${item.name} ${item.company_name || ""} ${item.phone || ""}`.toLocaleLowerCase("ru").includes(needle),
      )
    : clients;
  return (
    <>
      {selected && !listOpen && (
        <div className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3.5 shadow-sm">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-slate-900 text-sm font-bold text-white">
            {selected.name.slice(0, 1).toUpperCase()}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold text-slate-900">{selected.name}</div>
            <div className="truncate text-xs text-slate-500">
              {[selected.company_name, selected.phone].filter(Boolean).join(" · ") || "Без дополнительных данных"}
            </div>
          </div>
          {badge}
          {canChange && (
            <Button type="button" variant="outline" size="sm" onClick={onOpenList}>
              Изменить
            </Button>
          )}
        </div>
      )}

      {listOpen && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="relative border-b border-slate-100">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(event) => onSearch(event.target.value)}
              placeholder="Имя, компания или телефон…"
              className="h-12 w-full bg-transparent pl-10 pr-4 text-sm outline-none placeholder:text-slate-400"
              autoFocus={!value}
              aria-label="Поиск клиента"
            />
          </div>
          <div className="max-h-64 overflow-y-auto p-1.5">
            {filtered.map((item) => {
              const isSelected = String(item.id) === value;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onChoose(item)}
                  className={cn(
                    "flex w-full min-w-0 items-center gap-3 rounded-lg px-3 py-2 text-left transition",
                    isSelected ? "bg-slate-100" : "hover:bg-slate-50",
                  )}
                >
                  <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-bold text-slate-600">
                    {item.name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-slate-900">{item.name}</span>
                    <span className="block truncate text-xs text-slate-500">
                      {[item.company_name, item.phone].filter(Boolean).join(" · ") || "—"}
                    </span>
                  </span>
                  {item.department_name && (
                    <span className="hidden shrink-0 text-[11px] text-slate-400 sm:block">{item.department_name}</span>
                  )}
                  {isSelected && <Check className="size-4 shrink-0 text-slate-900" />}
                </button>
              );
            })}
            {!filtered.length && (
              <div className="flex min-h-24 flex-col items-center justify-center text-center text-slate-400">
                <span className="text-sm font-medium">Ничего не найдено</span>
                <span className="mt-0.5 text-xs">Проверьте имя или номер телефона.</span>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
