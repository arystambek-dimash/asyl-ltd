"use client";
import { useState } from "react";
import { Minus, Plus, Trash2 } from "lucide-react";
import { MAX_CART_QUANTITY } from "@/lib/cart";
import { cn } from "@/lib/utils";

/**
 * [−] N [+] как в Kaspi. Мешки заказывают сотнями, поэтому число — настоящее
 * поле ввода с цифровой клавиатурой, а не подпись между кнопками; на единице
 * минус превращается в корзину — нажатие убирает товар.
 */
export function QuantityStepper({
  value,
  onChange,
  label,
  autoFocus,
  className,
}: {
  value: number;
  onChange: (quantity: number) => void;
  label: string;
  /** Сразу поставить курсор в число: после «В корзину» количество обычно набирают. */
  autoFocus?: boolean;
  className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const buttonClass =
    "grid size-8 shrink-0 place-items-center rounded-md text-[var(--foreground)] transition-colors hover:bg-[var(--card)] active:bg-[var(--card)] disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]";
  return (
    <div
      role="group"
      aria-label={`Количество: ${label}`}
      className={cn("flex h-11 items-center gap-1 rounded-lg bg-[var(--muted)] p-1.5", className)}
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
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        enterKeyHint="done"
        autoComplete="off"
        autoFocus={autoFocus}
        value={draft ?? String(value)}
        onFocus={(event) => {
          const input = event.currentTarget;
          setDraft(String(value));
          // iOS снимает выделение, сделанное прямо в onFocus: выделяем после него — новое число заменит старое.
          setTimeout(() => input.select(), 0);
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
        className="h-8 w-0 min-w-0 flex-1 rounded-md border border-[var(--border)] bg-[var(--card)] text-center text-base font-semibold tabular-nums shadow-xs outline-none transition-[border-color,box-shadow] focus:border-[var(--ring)] focus:ring-2 focus:ring-[var(--ring)]/20 sm:text-sm"
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
