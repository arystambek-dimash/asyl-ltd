"use client";

import { useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import type { OrderProductOption } from "@/components/orders/order-form-parts";
import { FilterChips, ProductName } from "@/components/catalog/product-name";
import {
  brandKey,
  compareProductParts,
  productBrands,
  productParts,
  productSearch,
  productTitle,
  twinKey,
  twinKeys,
} from "@/lib/product-parts";
import { cn, formatCount } from "@/lib/utils";

const ALL_BRANDS = "";

/**
 * Выбор товара позиции заказа или «Возврата» — без цвета. Марка видна всегда: кнопками-фильтрами
 * вверху списка, первой строкой в каждом товаре и на самой кнопке. Список
 * раскрывается во всю ширину строки (это элемент её сетки): на телефоне сразу
 * под кнопкой, на компьютере (`sm:order-last`) под всей строкой. Поиск — по
 * марке, сорту, фасовке и кодам отчётов («Д1с»). Цвет дописывается, только
 * если без него двух товаров не различить.
 */
export function ProductPicker({
  products,
  value,
  onChange,
  bagsOf,
  allowOutOfStock,
  disabled,
  ariaLabel,
  className,
}: {
  products: OrderProductOption[];
  value: string;
  onChange: (productId: string) => void;
  /** Остаток мешков товара на выбранном складе. */
  bagsOf: (product: OrderProductOption) => number;
  /** Задним числом, правка отгруженного и «Возврат»: товар без остатка тоже можно выбрать. */
  allowOutOfStock: boolean;
  disabled?: boolean;
  ariaLabel: string;
  /** Размер кнопки в сетке строки. */
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [brand, setBrand] = useState(ALL_BRANDS);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listId = useId();

  const entries = useMemo(() => {
    const rows = products.map((product) => ({
      product,
      parts: productParts(product.name || product.label, product.weight_kg),
    }));
    const twins = twinKeys(rows.map(({ parts }) => parts));
    return rows.map((row) => ({ ...row, twin: twins.has(twinKey(row.parts)) }));
  }, [products]);

  // Марки в постоянном порядке: крупные (больше товаров) — первыми, поиск их не тасует.
  const brands = useMemo(() => productBrands(entries.map(({ parts }) => parts)), [entries]);
  // Выбранной марки нет среди товаров (сменили склад) — показываем все.
  const activeBrand = brands.some(({ key }) => key === brand) ? brand : ALL_BRANDS;

  const rows = useMemo(() => {
    const matches = productSearch(search);
    return entries
      .filter(({ parts }) => activeBrand === ALL_BRANDS || brandKey(parts.brand) === activeBrand)
      .filter(({ product, parts }) =>
        matches([product.name, product.label, productTitle(parts), ...(product.codes ?? [])]),
      )
      .sort((a, b) => compareProductParts(a.parts, b.parts, brands));
  }, [entries, brands, activeBrand, search]);

  const selected = entries.find((entry) => String(entry.product.id) === value);
  const firstAvailable = rows.find(({ product }) => allowOutOfStock || bagsOf(product) > 0);

  function close() {
    setOpen(false);
    setSearch("");
    setBrand(ALL_BRANDS);
    // Фокус — обратно на кнопку: иначе он падает на <body>, за пределы окна заказа.
    triggerRef.current?.focus();
  }

  function choose(productId: number) {
    // Тот же товар — просто закрыть: смена товара в форме сбрасывает цену строки.
    if (String(productId) !== value) onChange(String(productId));
    close();
  }

  /** Escape закрывает только список — не окно заказа с несохранёнными правками. */
  function onEscape(event: React.KeyboardEvent) {
    if (event.key !== "Escape" || !open) return;
    event.preventDefault();
    event.stopPropagation();
    close();
  }

  const selectedName = selected
    ? productTitle(selected.parts, selected.twin ? selected.product.color_label : undefined)
    : "";

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        // Выбранный товар — в имени кнопки: экранная читалка слышит не только «Товар, позиция 1».
        aria-label={selected ? `${ariaLabel}: ${selectedName}` : ariaLabel}
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={disabled}
        onClick={() => (open ? close() : setOpen(true))}
        onKeyDown={onEscape}
        className={cn(
          "flex min-h-10 w-full min-w-0 items-center gap-2 rounded-lg border border-[var(--border)] bg-white px-3 py-1 text-left text-sm shadow-sm transition",
          "hover:border-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]",
          "disabled:cursor-not-allowed disabled:opacity-60",
          open && "border-slate-400",
          className,
        )}
      >
        {selected ? (
          <ProductName parts={selected.parts} color={selected.twin ? selected.product.color_label : undefined} />
        ) : (
          <span className="min-w-0 flex-1 truncate text-slate-500">Выберите товар</span>
        )}
        <ChevronDown className={cn("size-4 shrink-0 text-slate-400 transition", open && "rotate-180")} />
      </button>

      {open && (
        <div
          id={listId}
          className="col-span-full overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm sm:order-last"
          onKeyDown={onEscape}
        >
          <div className="relative border-b border-slate-100">
            <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-slate-400" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                // Enter в поиске — выбрать первый подходящий, а не отправить форму заказа.
                if (event.key !== "Enter") return;
                event.preventDefault();
                // Пустой поиск — Enter ничего не меняет: иначе подменил бы уже выбранный товар.
                if (search.trim() && firstAvailable) choose(firstAvailable.product.id);
              }}
              placeholder="Поиск: Д1с, первый 25, KOROL…"
              className="h-11 w-full bg-transparent pl-10 pr-4 text-base outline-none placeholder:text-slate-400 sm:text-sm"
              aria-label="Поиск товара"
            />
          </div>

          <FilterChips
            label="Марка"
            options={brands}
            total={entries.length}
            value={activeBrand}
            onChange={setBrand}
            className="border-b border-slate-100 px-2 py-2"
          />

          <div role="listbox" aria-label="Товары" className="max-h-80 overflow-y-auto p-1.5">
            {rows.map(({ product, parts, twin }, index) => {
              const bags = bagsOf(product);
              const unavailable = bags <= 0 && !allowOutOfStock;
              const isSelected = String(product.id) === value;
              // Новая марка в общем списке — черта: DIKHAN и KOROL не сливаются.
              const newBrand = index > 0 && brandKey(rows[index - 1].parts.brand) !== brandKey(parts.brand);
              return (
                <button
                  key={product.id}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  disabled={unavailable}
                  onClick={() => choose(product.id)}
                  className={cn(
                    "flex w-full min-w-0 items-center gap-3 rounded-lg px-2.5 py-2 text-left transition",
                    isSelected ? "bg-slate-100" : "hover:bg-slate-50",
                    newBrand && "mt-1.5 rounded-t-none border-t border-slate-200 pt-2.5",
                    "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent",
                  )}
                >
                  <ProductName parts={parts} color={twin ? product.color_label : undefined} muted={unavailable} />
                  <span
                    className={cn(
                      "shrink-0 text-right text-xs tabular-nums",
                      bags > 0 ? "text-slate-600" : "text-slate-400",
                    )}
                  >
                    {bags > 0 ? `${formatCount(bags)} меш.` : allowOutOfStock ? "нет остатка" : "нет в наличии"}
                  </span>
                  {isSelected && <Check className="size-4 shrink-0 text-slate-900" />}
                </button>
              );
            })}
            {!rows.length && (
              <div className="flex min-h-20 flex-col items-center justify-center text-center text-slate-400">
                <span className="text-sm font-medium">Ничего не найдено</span>
                <span className="mt-0.5 text-xs">Попробуйте код («Д1с») или часть названия.</span>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
