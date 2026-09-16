"use client";
import { useState } from "react";
import { Minus, Plus, Trash2 } from "lucide-react";
import { MAX_CART_QUANTITY } from "@/lib/cart";
import { cn } from "@/lib/utils";

/**
 * [−] N [+] как в Kaspi. Мешки заказывают сотнями, поэтому число можно ввести
 * с клавиатуры; на единице минус превращается в корзину — нажатие убирает товар.
 */
export function QuantityStepper({
  value,
  onChange,
  label,
  className,
}: {
  value: number;
  onChange: (quantity: number) => void;
  label: string;
  className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const buttonClass =
    "grid w-10 shrink-0 place-items-center text-[var(--foreground)] transition-colors hover:bg-[var(--accent)] active:bg-[var(--accent)] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ring)]";
  return (
    <div
      role="group"
      aria-label={`Количество: ${label}`}
      className={cn("flex h-10 items-stretch overflow-hidden rounded-lg bg-[var(--muted)]", className)}
    >
      <button
        type="button"
        className={buttonClass}
        aria-label={value <= 1 ? `Убрать из корзины: ${label}` : `Меньше: ${label}`}
        onClick={() => onChange(value - 1)}
      >
        {value <= 1 ? <Trash2 className="size-4" /> : <Minus className="size-4" />}
      </button>
      <input
        aria-label={`Мешков: ${label}`}
        inputMode="numeric"
        value={draft ?? String(value)}
        onFocus={(event) => {
          setDraft(String(value));
          event.target.select();
        }}
        onChange={(event) => {
          const digits = event.target.value.replace(/\D/g, "").slice(0, String(MAX_CART_QUANTITY).length);
          setDraft(digits);
          // Ноль и пустое поле не применяем на лету: товар не должен исчезать, пока человек печатает.
          if (Number(digits) > 0) onChange(Number(digits));
        }}
        onBlur={() => setDraft(null)}
        onKeyDown={(event) => {
          if (event.key === "Enter") event.currentTarget.blur();
        }}
        className="w-0 min-w-0 flex-1 bg-transparent text-center text-base font-semibold tabular-nums outline-none sm:text-sm"
      />
      <button
        type="button"
        className={buttonClass}
        aria-label={`Больше: ${label}`}
        disabled={value >= MAX_CART_QUANTITY}
        onClick={() => onChange(value + 1)}
      >
        <Plus className="size-4" />
      </button>
    </div>
  );
}
