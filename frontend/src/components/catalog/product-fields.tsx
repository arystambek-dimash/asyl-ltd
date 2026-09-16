"use client";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { can } from "@/lib/can";
import type { Me } from "@/lib/types";

export interface ProductDraft {
  name: string;
  color: string;
  weight: string;
}

export const EMPTY_PRODUCT_DRAFT: ProductDraft = { name: "", color: "Red", weight: "50" };

const PRODUCT_COLORS = [
  ["Red", "Красный"],
  ["Green", "Зелёный"],
  ["Blue", "Синий"],
] as const;

const PRODUCT_WEIGHTS = ["50", "25", "10", "5", "2"];

/** Цвет (тип) товара виден тем, кто оформляет заказы или ведёт склад, — как и на сервере. */
export const canViewProductColor = (me: Me | null) => can(me, "orders.create") || can(me, "warehouse.view");

/** Тело запроса товара: без права на цвет новый товар получает «Красный», у старого цвет не трогаем. */
export function productPayload(
  draft: ProductDraft,
  { canViewColor, editing }: { canViewColor: boolean; editing: boolean },
) {
  return {
    name: draft.name.trim(),
    weight_kg: draft.weight,
    ...(canViewColor ? { color: draft.color } : editing ? {} : { color: "Red" }),
  };
}

/** Поля товара — общие для каталога и склада, чтобы сорт, цвет и фасовка заводились одинаково. */
export function ProductFields({
  idPrefix,
  draft,
  onChange,
  canViewColor,
  autoFocus,
}: {
  idPrefix: string;
  draft: ProductDraft;
  onChange: (draft: ProductDraft) => void;
  canViewColor: boolean;
  autoFocus?: boolean;
}) {
  return (
    <>
      <Field label="Название" htmlFor={`${idPrefix}-name`}>
        <Input
          id={`${idPrefix}-name`}
          value={draft.name}
          autoFocus={autoFocus}
          placeholder="напр. Высший сорт"
          onChange={(e) => onChange({ ...draft, name: e.target.value })}
          required
        />
      </Field>
      <div className={`grid grid-cols-1 gap-3 ${canViewColor ? "sm:grid-cols-2" : ""}`}>
        {canViewColor && (
          <Field label="Цвет (тип)" htmlFor={`${idPrefix}-color`}>
            <Select
              id={`${idPrefix}-color`}
              value={draft.color}
              onChange={(e) => onChange({ ...draft, color: e.target.value })}
            >
              {PRODUCT_COLORS.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </Field>
        )}
        <Field label="Фасовка" htmlFor={`${idPrefix}-weight`}>
          <Select
            id={`${idPrefix}-weight`}
            value={draft.weight}
            onChange={(e) => onChange({ ...draft, weight: e.target.value })}
          >
            {PRODUCT_WEIGHTS.map((weight) => (
              <option key={weight} value={weight}>
                {weight} кг
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </>
  );
}
