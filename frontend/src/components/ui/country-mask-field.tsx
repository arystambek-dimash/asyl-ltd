"use client";
import * as React from "react";
import { ChevronDown, Globe } from "lucide-react";
import { cn } from "@/lib/utils";

/** Поле с маской (телефон, номер машины) передаёт aria-invalid как есть: true или "true". */
export const isAriaInvalid = (value: React.AriaAttributes["aria-invalid"]) => value === true || value === "true";

/** Классы самого <input> внутри CountryMaskField; размер текста — тот же textClassName, что у поля. */
export const COUNTRY_MASK_INPUT = "relative h-full w-full bg-transparent px-3 outline-none disabled:cursor-not-allowed";

/**
 * Курсор после перерисовки маски: handleInput кладёт в ref, сколько значимых
 * знаков стоит до курсора, а эффект ставит его за столько же знаков нового
 * текста. Правка в середине не отбрасывает курсор в конец.
 */
export function useCaretRestore(
  inputRef: React.RefObject<HTMLInputElement | null>,
  text: string,
  counts: (char: string) => boolean,
) {
  const pendingCaret = React.useRef<number | null>(null);
  React.useLayoutEffect(() => {
    const input = inputRef.current;
    if (pendingCaret.current === null || !input || document.activeElement !== input) return;
    let position = text.length;
    for (let index = 0, seen = 0; index < text.length; index++) {
      if (seen === pendingCaret.current) {
        position = index;
        break;
      }
      if (counts(text[index])) seen++;
    }
    pendingCaret.current = null;
    input.setSelectionRange(position, position);
  });
  return pendingCaret;
}

/**
 * Оболочка поля «страна + значение по маске»: слева флаг с прозрачным
 * <select> страны (нет страны — глобус), справа поле с серым недобранным
 * хвостом маски. Сам <input> (с COUNTRY_MASK_INPUT) передаётся в children.
 */
export function CountryMaskField({
  badge,
  country,
  text,
  hint,
  textClassName,
  invalid,
  disabled,
  className,
  children,
}: {
  /** Флаг и код выбранной страны; null — «Другая», рисуется глобус. */
  badge: React.ReactNode | null;
  country: {
    id?: string;
    label: string;
    value: string;
    options: { value: string; label: string }[];
    onChange: (event: React.ChangeEvent<HTMLSelectElement>) => void;
  };
  text: string;
  hint: string;
  textClassName: string;
  invalid: boolean;
  disabled?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex h-10 w-full overflow-hidden rounded-md border border-[var(--input)] bg-[var(--background)] shadow-sm transition-[border-color,box-shadow] focus-within:border-[var(--ring)] focus-within:ring-2 focus-within:ring-[var(--ring)]/20",
        invalid &&
          "border-[var(--destructive)] focus-within:border-[var(--destructive)] focus-within:ring-[var(--destructive)]/20",
        disabled && "cursor-not-allowed bg-[var(--muted)] opacity-70",
        className,
      )}
    >
      <div className="relative flex shrink-0 items-center gap-1.5 border-r border-[var(--input)] pl-3 pr-2 text-sm transition-colors hover:bg-[var(--accent)]">
        {badge ?? <Globe aria-hidden className="size-4 text-[var(--muted-foreground)]" />}
        <ChevronDown aria-hidden className="size-3.5 text-[var(--muted-foreground)]" />
        <select
          id={country.id}
          aria-label={country.label}
          value={country.value}
          onChange={country.onChange}
          disabled={disabled}
          className="absolute inset-0 cursor-pointer opacity-0 disabled:cursor-not-allowed"
        >
          {country.options.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </div>
      {/* 16px на телефоне: iOS не зумит страницу при фокусе. Подсказка того же размера, иначе съедет. */}
      <div className="relative min-w-0 flex-1">
        {hint && (
          <div
            aria-hidden
            className={cn(
              "pointer-events-none absolute inset-0 flex items-center overflow-hidden whitespace-pre px-3",
              textClassName,
            )}
          >
            <span className="invisible">{text}</span>
            <span className="text-[var(--muted-foreground)]/60">{hint}</span>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
