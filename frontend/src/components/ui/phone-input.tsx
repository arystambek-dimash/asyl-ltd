"use client";
import * as React from "react";
import {
  COUNTRY_MASK_INPUT,
  CountryMaskField,
  isAriaInvalid,
  useCaretRestore,
} from "@/components/ui/country-mask-field";
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

const isDigit = (char: string) => /\d/.test(char);

const COUNTRY_OPTIONS = [
  ...MARKET_COUNTRIES.map((country) => ({
    value: country.name,
    label: `${countryFlag(country.iso)} ${country.name} +${country.dial}`,
  })),
  { value: OTHER_COUNTRY, label: "🌐 Другая страна" },
];
const TEXT = "text-base tabular-nums sm:text-sm";

export const PhoneInput = React.forwardRef<HTMLInputElement, PhoneInputProps>(
  ({ value, onChange, defaultCountry, onCountryChange, className, disabled, id, ...props }, ref) => {
    const inputRef = React.useRef<HTMLInputElement>(null);
    React.useImperativeHandle(ref, () => inputRef.current as HTMLInputElement);
    const [preferred, setPreferred] = React.useState<PhoneCountry>(() =>
      defaultCountry === OTHER_COUNTRY ? null : (findCountry(defaultCountry) ?? DEFAULT_PHONE_COUNTRY),
    );

    const parts = parsePhone(value, preferred);
    const text = displayText(parts);
    const hint = parts.country
      ? maskHint(parts.country.mask, parts.digits.length)
      : parts.digits
        ? ""
        : "код страны и номер";
    const pendingCaret = useCaretRestore(inputRef, text, isDigit);

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
      <CountryMaskField
        badge={
          parts.country && (
            <>
              <span aria-hidden className="text-base leading-none">
                {countryFlag(parts.country.iso)}
              </span>
              <span className="tabular-nums">+{parts.country.dial}</span>
            </>
          )
        }
        country={{
          id: id ? `${id}-country` : undefined,
          label: "Страна телефона",
          value: parts.country?.name ?? OTHER_COUNTRY,
          options: COUNTRY_OPTIONS,
          onChange: handleCountry,
        }}
        text={text}
        hint={hint}
        textClassName={TEXT}
        invalid={isAriaInvalid(props["aria-invalid"])}
        disabled={disabled}
        className={className}
      >
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
          className={cn(COUNTRY_MASK_INPUT, TEXT)}
        />
      </CountryMaskField>
    );
  },
);
PhoneInput.displayName = "PhoneInput";
