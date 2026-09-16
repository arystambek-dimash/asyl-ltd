"use client";
import * as React from "react";
import { ChevronDown, Globe } from "lucide-react";
import { MARKET_COUNTRIES, OTHER_COUNTRY, countryFlag, findCountry } from "@/lib/countries";
import {
  DEFAULT_PHONE_COUNTRY,
  composePhone,
  formatNational,
  maskHint,
  onlyDigits,
  parsePhone,
  phoneSlots,
  readPhoneInput,
  type PhoneCountry,
  type PhoneParts,
} from "@/lib/phone";
import { cn } from "@/lib/utils";

export interface PhoneInputProps extends Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "value" | "defaultValue" | "onChange" | "type"
> {
  /** Номер целиком: «+7 (705) 565-65-65». */
  value: string;
  onChange: (value: string) => void;
  /** Страна для пустого поля — название из COUNTRIES. */
  defaultCountry?: string;
  /** Выбранная страна — название из COUNTRIES («Другая» для прочих). */
  onCountryChange?: (country: string) => void;
}

const displayText = ({ country, digits }: PhoneParts) =>
  country ? formatNational(country.mask, digits) : `+${digits}`;

function caretAfterDigits(text: string, count: number): number {
  let seen = 0;
  for (let index = 0; index < text.length; index++) {
    if (seen === count) return index;
    if (/\d/.test(text[index])) seen++;
  }
  return text.length;
}

export const PhoneInput = React.forwardRef<HTMLInputElement, PhoneInputProps>(
  ({ value, onChange, defaultCountry, onCountryChange, className, disabled, id, ...props }, ref) => {
    const inputRef = React.useRef<HTMLInputElement>(null);
    React.useImperativeHandle(ref, () => inputRef.current as HTMLInputElement);
    const [preferred, setPreferred] = React.useState<PhoneCountry>(() =>
      defaultCountry === OTHER_COUNTRY ? null : (findCountry(defaultCountry) ?? DEFAULT_PHONE_COUNTRY),
    );
    const pendingCaret = React.useRef<number | null>(null);

    const parts = parsePhone(value, preferred);
    const text = displayText(parts);
    const hint = parts.country
      ? maskHint(parts.country.mask, parts.digits.length)
      : parts.digits
        ? ""
        : "код страны и номер";
    const invalid = props["aria-invalid"] === true || props["aria-invalid"] === "true";

    React.useLayoutEffect(() => {
      const input = inputRef.current;
      if (pendingCaret.current === null || !input || document.activeElement !== input) return;
      const position = caretAfterDigits(text, pendingCaret.current);
      pendingCaret.current = null;
      input.setSelectionRange(position, position);
    });

    function emit(next: PhoneParts) {
      if (next.country?.name !== parts.country?.name) {
        setPreferred(next.country);
        onCountryChange?.(next.country?.name ?? OTHER_COUNTRY);
      }
      onChange(composePhone(next));
    }

    function handleInput(event: React.ChangeEvent<HTMLInputElement>) {
      const raw = event.target.value;
      const caret = event.target.selectionStart ?? raw.length;
      const next = readPhoneInput(raw, parts.country);
      // Правка в середине номера оставляет курсор на месте, а не отбрасывает в конец.
      pendingCaret.current =
        caret < raw.length && next.country?.name === parts.country?.name
          ? onlyDigits(raw.slice(0, caret)).length
          : Number.POSITIVE_INFINITY;
      emit(next);
    }

    function handleCountry(event: React.ChangeEvent<HTMLSelectElement>) {
      const country = findCountry(event.target.value);
      // Цифры без кода переносим в новую страну; номер «другой страны» с кодом не перенести.
      const digits = country && parts.country ? parts.digits.slice(0, phoneSlots(country)) : "";
      emit({ country, digits });
      inputRef.current?.focus();
    }

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
          {parts.country ? (
            <>
              <span aria-hidden className="text-base leading-none">
                {countryFlag(parts.country.iso)}
              </span>
              <span className="tabular-nums">+{parts.country.dial}</span>
            </>
          ) : (
            <Globe aria-hidden className="size-4 text-[var(--muted-foreground)]" />
          )}
          <ChevronDown aria-hidden className="size-3.5 text-[var(--muted-foreground)]" />
          <select
            id={id ? `${id}-country` : undefined}
            aria-label="Страна телефона"
            value={parts.country?.name ?? OTHER_COUNTRY}
            onChange={handleCountry}
            disabled={disabled}
            className="absolute inset-0 cursor-pointer opacity-0 disabled:cursor-not-allowed"
          >
            {MARKET_COUNTRIES.map((country) => (
              <option key={country.name} value={country.name}>
                {countryFlag(country.iso)} {country.name} +{country.dial}
              </option>
            ))}
            <option value={OTHER_COUNTRY}>🌐 Другая страна</option>
          </select>
        </div>
        {/* 16px на телефоне: iOS не зумит страницу при фокусе. Подсказка того же размера, иначе съедет. */}
        <div className="relative min-w-0 flex-1">
          {hint && (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0 flex items-center overflow-hidden whitespace-pre px-3 text-base tabular-nums sm:text-sm"
            >
              <span className="invisible">{text}</span>
              <span className="text-[var(--muted-foreground)]/60">{hint}</span>
            </div>
          )}
          <input
            ref={inputRef}
            id={id}
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            {...props}
            value={text}
            onChange={handleInput}
            disabled={disabled}
            className="relative h-full w-full bg-transparent px-3 text-base tabular-nums outline-none disabled:cursor-not-allowed sm:text-sm"
          />
        </div>
      </div>
    );
  },
);
PhoneInput.displayName = "PhoneInput";
