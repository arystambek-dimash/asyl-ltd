"use client";
import Link from "next/link";
import { ArrowUpRight, Store } from "lucide-react";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { EmployeeShopSettings } from "@/lib/types";

export interface ShopForm {
  enabled: boolean;
  name: string;
}

export function shopFormFrom(shop: EmployeeShopSettings | null | undefined): ShopForm {
  return shop ? { enabled: shop.enabled, name: shop.name } : { enabled: false, name: "" };
}

/** Поле ``shop`` запроса: магазин, который не включали и не включают, не трогаем. */
export function shopPayload(form: ShopForm, existing: EmployeeShopSettings | null | undefined) {
  if (!form.enabled && !existing) return undefined;
  return { enabled: form.enabled, name: form.name.trim() };
}

/** «Свой магазин» в карточке сотрудника: заказы без грузчика и без списания склада. */
export function EmployeeShopFields({
  value,
  onChange,
  existing,
  defaultName,
  disabled,
}: {
  value: ShopForm;
  onChange: (next: ShopForm) => void;
  existing: EmployeeShopSettings | null | undefined;
  defaultName: string;
  disabled: boolean;
}) {
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
            Сотрудник сам заказывает в «Моём магазине»: без грузчика, склад не списывается. Цены — из прайса магазина,
            неоплаченное — долг магазина в кассе.
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
