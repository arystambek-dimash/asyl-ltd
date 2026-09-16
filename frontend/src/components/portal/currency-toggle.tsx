"use client";
import type { CartCurrency } from "@/lib/cart";
import { cn, currencySymbol } from "@/lib/utils";

/** Валюта личного прайса: цены каталога, корзины и заказа — в ней. */
export function CurrencyToggle({
  value,
  onChange,
  className,
}: {
  value: CartCurrency;
  onChange: (currency: CartCurrency) => void;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label="Валюта"
      className={cn("inline-flex rounded-lg border bg-[var(--muted)]/30 p-1", className)}
    >
      {(["KZT", "USD"] as const).map((code) => (
        <button
          key={code}
          type="button"
          aria-pressed={value === code}
          onClick={() => onChange(code)}
          className={cn(
            "flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-semibold transition-all",
            value === code ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm" : "text-[var(--muted-foreground)]",
          )}
        >
          {code} {currencySymbol(code)}
        </button>
      ))}
    </div>
  );
}
