"use client";
import Link from "next/link";
import { ArrowUpRight, Store } from "lucide-react";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { EmployeeShopSettings, Warehouse } from "@/lib/types";

export interface ShopForm {
  enabled: boolean;
  name: string;
  /** id склада строкой; пусто — прежний склад магазина, у нового — основной. */
  warehouse: string;
}

export function shopFormFrom(shop: EmployeeShopSettings | null | undefined): ShopForm {
  return shop
    ? { enabled: shop.enabled, name: shop.name, warehouse: String(shop.warehouse) }
    : { enabled: false, name: "", warehouse: "" };
}

/** Поле ``shop`` запроса: магазин, который не включали и не включают, не трогаем. */
export function shopPayload(form: ShopForm, existing: EmployeeShopSettings | null | undefined) {
  if (!form.enabled && !existing) return undefined;
  return {
    enabled: form.enabled,
    name: form.name.trim(),
    warehouse: form.warehouse ? Number(form.warehouse) : null,
  };
}

/** «Свой магазин» в карточке сотрудника: получатель пополнений и отдельные продажи. */
export function EmployeeShopFields({
  value,
  onChange,
  existing,
  defaultName,
  warehouses,
  disabled,
}: {
  value: ShopForm;
  onChange: (next: ShopForm) => void;
  existing: EmployeeShopSettings | null | undefined;
  defaultName: string;
  /** null — список складов не открыт этому администратору: сервер возьмёт склад сам. */
  warehouses: Warehouse[] | null | undefined;
  disabled: boolean;
}) {
  const active = (warehouses ?? []).filter(
    (warehouse) => warehouse.is_active || String(warehouse.id) === value.warehouse,
  );
  return (
    <section className="space-y-3">
      <label className="flex cursor-pointer items-start gap-3 rounded-2xl border bg-[var(--muted)]/45 p-4">
        <input
          type="checkbox"
          checked={value.enabled}
          disabled={disabled}
          onChange={(event) => onChange({ ...value, enabled: event.target.checked })}
        />
        <span>
          <span className="flex items-center gap-1.5 text-sm font-bold">
            <Store className="size-4" /> Свой магазин
          </span>
          <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
            Пополнение — обычным заказом на магазин, через грузчика. Продажи в «Заказах магазина» списывают только
            собственный остаток магазина. Цены — из прайса магазина.
          </span>
        </span>
      </label>

      {value.enabled && (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Название магазина" htmlFor="employee-shop-name">
            <Input
              id="employee-shop-name"
              value={value.name}
              placeholder={defaultName}
              maxLength={150}
              disabled={disabled}
              onChange={(event) => onChange({ ...value, name: event.target.value })}
            />
          </Field>
          {active.length > 1 && (
            <Field label="Склад пополнения" htmlFor="employee-shop-warehouse">
              <Select
                id="employee-shop-warehouse"
                value={value.warehouse}
                disabled={disabled}
                onChange={(event) => onChange({ ...value, warehouse: event.target.value })}
              >
                {!value.warehouse && <option value="">Основной склад</option>}
                {active.map((warehouse) => (
                  <option key={warehouse.id} value={warehouse.id}>
                    {warehouse.name}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <p className="text-xs text-[var(--muted-foreground)] sm:col-span-2">
            {existing ? (
              <Link
                href={`/clients/${existing.client}/prices`}
                className="inline-flex items-center gap-1 font-medium text-[var(--foreground)] underline-offset-2 hover:underline"
              >
                Прайс магазина <ArrowUpRight className="size-3.5" />
              </Link>
            ) : (
              "После сохранения магазин появится в «Клиентах» — "
            )}
            {existing ? " — цены ставит бухгалтер, как клиентам." : "бухгалтер поставит ему цены, как клиентам."}
          </p>
        </div>
      )}
    </section>
  );
}
