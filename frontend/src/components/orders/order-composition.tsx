"use client";
import { Trash2 } from "lucide-react";
import { ProductPicker } from "@/components/orders/product-picker";
import type { OrderProductOption } from "@/components/orders/order-form-parts";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { currencySymbol, formatCurrency } from "@/lib/utils";

/** Общая таблица состава обычного заказа и продажи магазина. */
export function OrderComposition({
  currency,
  children,
  footer,
}: {
  currency: string;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <div className="hidden grid-cols-[minmax(0,1fr)_96px_128px_112px_36px] gap-3 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500 sm:grid">
        <span>Товар</span>
        <span>Мешков</span>
        <span>Цена, {currencySymbol(currency)}</span>
        <span className="text-right">Сумма</span>
        <span />
      </div>
      <div className="divide-y divide-slate-100">{children}</div>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-slate-100 bg-slate-50/60 px-4 py-2.5">
        {footer}
      </div>
    </div>
  );
}

export function OrderCompositionRow({
  row,
  index,
  products,
  currency,
  disabled,
  hintOf,
  onProduct,
  onQuantity,
  onPrice,
  onRemove,
  priceContent,
  children,
}: {
  row: { product: string; quantity: string; price: string; bonus?: boolean };
  index: number;
  products: OrderProductOption[];
  currency: string;
  disabled?: boolean;
  hintOf: (product: OrderProductOption) => { text: string; muted?: boolean };
  onProduct: (product: string) => void;
  onQuantity: (quantity: string) => void;
  onPrice: (price: string) => void;
  onRemove: () => void;
  priceContent?: React.ReactNode;
  children?: React.ReactNode;
}) {
  const lineTotal = row.bonus ? 0 : Number(row.price || 0) * Number(row.quantity || 0);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_36px] gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_96px_128px_112px_36px] sm:gap-3 sm:items-center">
      <ProductPicker
        products={products}
        value={row.product}
        className="col-span-3 sm:col-span-1"
        ariaLabel={`Товар, позиция ${index + 1}`}
        disabled={disabled}
        hintOf={hintOf}
        onChange={onProduct}
      />
      <Input
        type="number"
        min="1"
        inputMode="numeric"
        placeholder="Мешков"
        className="h-10 rounded-lg"
        value={row.quantity}
        aria-label={`Количество мешков, позиция ${index + 1}`}
        disabled={disabled}
        onChange={(event) => onQuantity(event.target.value)}
      />
      {priceContent ?? (
        <Input
          type="number"
          min="0"
          step="0.01"
          inputMode="decimal"
          className="h-10 rounded-lg"
          aria-label={`Цена, позиция ${index + 1}`}
          placeholder={`Цена, ${currencySymbol(currency)}`}
          value={row.price}
          disabled={disabled}
          onChange={(event) => onPrice(event.target.value)}
        />
      )}
      <div className="col-span-2 self-center text-sm font-semibold tabular-nums text-slate-900 sm:col-span-1 sm:text-right">
        {row.bonus ? (
          <span className="font-medium text-slate-500">бесплатно</span>
        ) : lineTotal > 0 ? (
          formatCurrency(String(lineTotal), currency)
        ) : (
          <span className="text-slate-300">—</span>
        )}
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="justify-self-end text-slate-400 hover:text-red-600"
        title="Удалить позицию"
        aria-label={`Удалить позицию ${index + 1}`}
        disabled={disabled}
        onClick={onRemove}
      >
        <Trash2 className="size-4" />
      </Button>
      {children}
    </div>
  );
}
