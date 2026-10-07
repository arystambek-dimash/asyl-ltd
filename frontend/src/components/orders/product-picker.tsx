"use client";

import { useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Search } from "lucide-react";
import type { OrderProductOption } from "@/components/orders/order-form-parts";
import { lookalikeKey } from "@/lib/plates";
import { cn, formatCount } from "@/lib/utils";

/**
 * «Первый сорт DIKHAN BABA NAN 50кг» → сорт «Первый сорт», марка «DIKHAN BABA NAN».
 * Сорт может стоять и после марки («KOROL высший сорт 50кг»).
 */
const GRADE = /(высший|первый|второй|третий)\s+сорт/i;
const GRADE_RANK = ["высший", "первый", "второй", "третий"];
const WEIGHT_SUFFIX = /\s*\d+(?:[.,]\d+)?\s*(?:кг|kg)\.?\s*$/i;
const ALL_BRANDS = "";

interface ProductParts {
  brand: string;
  grade: string;
  weight: number;
}

/** Ключ марки: регистр и пробелы не делают из одной марки две кнопки. */
function brandKey(brand: string) {
  return brand.toLocaleUpperCase("ru");
}

function productParts(product: OrderProductOption): ProductParts {
  const name = (product.name || product.label).replace(/\s+/g, " ").trim();
  const grade = name.match(GRADE)?.[1]?.toLocaleLowerCase("ru") ?? "";
  const brand = name.replace(GRADE, " ").replace(WEIGHT_SUFFIX, "").replace(/\s+/g, " ").trim() || name;
  return {
    brand,
    grade: grade ? `${grade[0].toLocaleUpperCase("ru")}${grade.slice(1)} сорт` : "",
    weight: Number(product.weight_kg) || 0,
  };
}

function gradeRank(grade: string) {
  const rank = GRADE_RANK.indexOf(grade.split(" ")[0].toLocaleLowerCase("ru"));
  return rank < 0 ? GRADE_RANK.length : rank;
}

/** «Высший сорт · 50 кг» — сорт и фасовка без марки. */
function gradeLine(parts: ProductParts) {
  return [parts.grade, parts.weight ? `${parts.weight} кг` : ""].filter(Boolean).join(" · ");
}

function WeightChip({ weight, muted }: { weight: number; muted?: boolean }) {
  if (!weight) return null;
  return (
    <span
      className={cn(
        "shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold tabular-nums",
        muted ? "bg-slate-200 text-slate-600" : "bg-slate-900 text-white",
      )}
    >
      {weight} кг
    </span>
  );
}

/** Две строки товара: марка крупными заглавными, под ней сорт и фасовка. */
function ProductLines({ parts, color, muted }: { parts: ProductParts; color?: string; muted?: boolean }) {
  return (
    <span className="min-w-0 flex-1">
      {/* Марка — главная строка: крупно и тёмным, по ней и ищут глазами. */}
      <span className="block truncate text-sm font-bold uppercase leading-5 tracking-wide text-slate-900">
        {parts.brand}
        {color && <span className="font-medium normal-case text-slate-500"> · {color}</span>}
      </span>
      <span className="mt-0.5 flex min-w-0 items-center gap-2 leading-5">
        {/* Без сорта в названии («Мука 50 кг») — только фасовка: марка уже строкой выше. */}
        {parts.grade && <span className="truncate text-sm text-slate-600">{parts.grade}</span>}
        <WeightChip weight={parts.weight} muted={muted} />
      </span>
    </span>
  );
}

/**
 * Выбор товара позиции заказа — без цвета. Марка видна всегда: кнопками-фильтрами
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
  /** Задним числом и правка отгруженного: товар без остатка тоже можно выбрать. */
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
    const rows = products.map((product) => ({ product, parts: productParts(product) }));
    const key = (parts: ProductParts) => `${brandKey(parts.brand)}|${gradeLine(parts)}`.toLocaleLowerCase("ru");
    const counts = new Map<string, number>();
    for (const { parts } of rows) counts.set(key(parts), (counts.get(key(parts)) ?? 0) + 1);
    return rows.map((row) => ({ ...row, twin: (counts.get(key(row.parts)) ?? 0) > 1 }));
  }, [products]);

  // Марки в постоянном порядке: крупные (больше товаров) — первыми, поиск их не тасует.
  const brands = useMemo(() => {
    // Ключ — марка без учёта регистра; подпись — как написана у первого товара.
    const byKey = new Map<string, { name: string; count: number }>();
    for (const { parts } of entries) {
      const found = byKey.get(brandKey(parts.brand));
      byKey.set(brandKey(parts.brand), { name: found?.name ?? parts.brand, count: (found?.count ?? 0) + 1 });
    }
    return [...byKey.entries()]
      .sort(([a, x], [b, y]) => y.count - x.count || a.localeCompare(b, "ru"))
      .map(([key, { name, count }]) => ({ key, name, count }));
  }, [entries]);
  // Выбранной марки нет среди товаров (сменили склад) — показываем все.
  const activeBrand = brands.some(({ key }) => key === brand) ? brand : ALL_BRANDS;

  const rows = useMemo(() => {
    // Каждое слово запроса — в любом месте: «первый 25», «д1с», «korol 50»;
    // кириллические двойники — как латиница: «Д1с» найдёт и код «Д1c».
    const words = lookalikeKey(search).split(/\s+/).filter(Boolean);
    const brandOrder = new Map(brands.map(({ key }, index) => [key, index]));
    return entries
      .filter(({ parts }) => activeBrand === ALL_BRANDS || brandKey(parts.brand) === activeBrand)
      .filter(({ product, parts }) => {
        if (!words.length) return true;
        const haystack = lookalikeKey(
          [product.name, product.label, parts.brand, parts.grade, `${parts.weight} кг`, ...(product.codes ?? [])].join(
            " ",
          ),
        );
        return words.every((word) => haystack.includes(word));
      })
      .sort(
        (a, b) =>
          (brandOrder.get(brandKey(a.parts.brand)) ?? 0) - (brandOrder.get(brandKey(b.parts.brand)) ?? 0) ||
          gradeRank(a.parts.grade) - gradeRank(b.parts.grade) ||
          b.parts.weight - a.parts.weight,
      );
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
    ? [selected.parts.brand, gradeLine(selected.parts), selected.twin ? selected.product.color_label : ""]
        .filter(Boolean)
        .join(" · ")
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
          <ProductLines parts={selected.parts} color={selected.twin ? selected.product.color_label : undefined} />
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

          {brands.length > 1 && (
            <div
              role="radiogroup"
              aria-label="Марка"
              className="flex flex-wrap gap-1.5 border-b border-slate-100 px-2 py-2"
            >
              {[{ key: ALL_BRANDS, name: "Все", count: entries.length }, ...brands].map(({ key, name, count }) => {
                const active = activeBrand === key;
                return (
                  <button
                    key={key || "all"}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => setBrand(key)}
                    className={cn(
                      "shrink-0 rounded-full border px-3 py-1.5 text-sm font-semibold transition",
                      active
                        ? "border-slate-900 bg-slate-900 text-white"
                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
                    )}
                  >
                    {name}
                    <span className={cn("ml-1.5 text-xs font-medium", active ? "text-white/70" : "text-slate-400")}>
                      {count}
                    </span>
                  </button>
                );
              })}
            </div>
          )}

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
                  <ProductLines parts={parts} color={twin ? product.color_label : undefined} muted={unavailable} />
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
