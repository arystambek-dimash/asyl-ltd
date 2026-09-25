"use client";
import * as React from "react";
import { Chip } from "@/components/ui/chip";
import {
  COUNTRY_MASK_INPUT,
  CountryMaskField,
  isAriaInvalid,
  useCaretRestore,
} from "@/components/ui/country-mask-field";
import { MARKET_COUNTRIES, OTHER_COUNTRY, countryFlag } from "@/lib/countries";
import {
  PLATE_COUNTRIES,
  formatPlatePair,
  normalizePlate,
  plateCountryFor,
  plateCountryIso,
  plateDisplay,
  plateWarning,
  readPlateInput,
  sameTransportPair,
  type PlateCountry,
  type PlateKind,
  type TransportPair,
} from "@/lib/plates";
import { cn } from "@/lib/utils";

export interface PlateInputProps extends Omit<
  React.InputHTMLAttributes<HTMLInputElement>,
  "value" | "defaultValue" | "onChange" | "type" | "size"
> {
  /** Номер слитно: «07KG695ADT». */
  value: string;
  onChange: (value: string) => void;
  kind?: PlateKind;
  /** Страна клиента — название из COUNTRIES (Client.country): маска пустого поля. */
  defaultCountry?: string;
  /** lg — крупное поле экрана грузчика. */
  size?: "md" | "lg";
  /** Показать мягкое предупреждение о незнакомом формате, когда поле покинули. */
  warning?: boolean;
}

const COUNTRY_NAMES = Object.fromEntries(MARKET_COUNTRIES.map((country) => [country.iso, country.name]));
const COUNTRY_OPTIONS = [
  ...PLATE_COUNTRIES.map((iso) => ({ value: iso, label: `${countryFlag(iso)} ${COUNTRY_NAMES[iso] ?? iso}` })),
  { value: "", label: `🌐 ${OTHER_COUNTRY}` },
];
const isPlateChar = (char: string) => char !== " ";

/**
 * Номер машины одним полем: флаг страны с прозрачным выбором KZ/KG/UZ/RU/Другая,
 * группы по маске страны и серый недобранный хвост. Правила — в lib/plates.
 */
export const PlateInput = React.forwardRef<HTMLInputElement, PlateInputProps>(
  (
    {
      value,
      onChange,
      kind = "truck",
      defaultCountry,
      size = "md",
      warning = false,
      className,
      disabled,
      onFocus,
      onBlur,
      ...props
    },
    ref,
  ) => {
    const inputRef = React.useRef<HTMLInputElement>(null);
    React.useImperativeHandle(ref, () => inputRef.current as HTMLInputElement);
    // Страна, выбранная вручную; undefined — не выбирали: действует страна клиента.
    // Угаданную по номеру не запоминаем: начало номера бывает полным номером
    // другой страны, и она залипала бы на неоднозначном номере.
    const [picked, setPicked] = React.useState<PlateCountry | null | undefined>(undefined);
    const [focused, setFocused] = React.useState(false);

    const preferred = picked === undefined ? plateCountryIso(defaultCountry) : picked;
    const compact = normalizePlate(value);
    const country = plateCountryFor(compact, preferred, kind);
    const display = plateDisplay(compact, country, kind);
    // Старый свободный текст («самовывоз») показываем как записан.
    const text = /^[0-9A-Z]*$/.test(compact) ? display.text : value;
    const hint = text === display.text ? display.hint : "";
    const invalid = isAriaInvalid(props["aria-invalid"]);
    // Номер с ошибкой форма объясняет сама — мягкое предупреждение её бы только дублировало.
    const softWarning = warning && !focused && !invalid ? plateWarning(compact, kind) : null;
    const lg = size === "lg";
    const textClass = lg
      ? "text-2xl font-bold tracking-wide tabular-nums"
      : "text-base font-medium tracking-wide tabular-nums sm:text-sm";

    const pendingCaret = useCaretRestore(inputRef, text, isPlateChar);

    function handleInput(event: React.ChangeEvent<HTMLInputElement>) {
      const raw = event.target.value;
      const caret = event.target.selectionStart ?? raw.length;
      const head = readPlateInput(raw.slice(0, caret));
      const tail = readPlateInput(raw.slice(caret));
      let next = readPlateInput(raw);
      let before = head.length;
      if (next === compact && raw.length < text.length) {
        // Стёрли пробел между группами — стираем соседний знак, иначе клавиша упрётся в пробел.
        const forward = (event.nativeEvent as InputEvent).inputType === "deleteContentForward";
        if (forward) next = head + tail.slice(1);
        else if (head) {
          next = head.slice(0, -1) + tail;
          before -= 1;
        }
      }
      // Правка в середине номера оставляет курсор на месте, а не отбрасывает в конец.
      pendingCaret.current = caret < raw.length ? before : Number.POSITIVE_INFINITY;
      onChange(next);
    }

    function handleCountry(event: React.ChangeEvent<HTMLSelectElement>) {
      setPicked((event.target.value || null) as PlateCountry | null);
      inputRef.current?.focus();
    }

    const control = (
      <CountryMaskField
        badge={
          country && (
            <>
              <span aria-hidden className="text-base leading-none">
                {countryFlag(country)}
              </span>
              <span className="text-xs font-semibold">{country}</span>
            </>
          )
        }
        country={{
          label: kind === "trailer" ? "Страна прицепа" : "Страна тягача",
          value: country ?? "",
          options: COUNTRY_OPTIONS,
          onChange: handleCountry,
        }}
        text={text}
        hint={hint}
        textClassName={textClass}
        invalid={invalid}
        disabled={disabled}
        className={cn(lg && "h-14", className)}
      >
        <input
          ref={inputRef}
          type="text"
          autoCapitalize="characters"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          {...props}
          value={text}
          onChange={handleInput}
          onFocus={(event) => {
            setFocused(true);
            onFocus?.(event);
          }}
          onBlur={(event) => {
            setFocused(false);
            onBlur?.(event);
          }}
          disabled={disabled}
          className={cn(COUNTRY_MASK_INPUT, textClass)}
        />
      </CountryMaskField>
    );
    if (!warning) return control;
    return (
      <div className="grid gap-1">
        {control}
        {softWarning && (
          <p role="status" className="text-xs text-[var(--warning)]">
            {softWarning}
          </p>
        )}
      </div>
    );
  },
);
PlateInput.displayName = "PlateInput";

/** Чипы «как в прошлый раз»: прошлые пары клиента, нажатие подставляет пару целиком. */
export function PlateSuggestions({
  suggestions,
  current,
  onPick,
}: {
  suggestions: TransportPair[];
  current: TransportPair;
  onPick: (pair: TransportPair) => void;
}) {
  if (!suggestions.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-[var(--muted-foreground)]">Как в прошлый раз:</span>
      {suggestions.map((pair) => (
        <Chip
          key={`${pair.truck_number}/${pair.trailer_number}`}
          active={sameTransportPair(pair, current)}
          onClick={() => onPick(pair)}
          className="tabular-nums"
        >
          {formatPlatePair(pair.truck_number, pair.trailer_number)}
        </Chip>
      ))}
    </div>
  );
}
