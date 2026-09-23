"use client";
import * as React from "react";
import { ChevronDown, Globe } from "lucide-react";
import { Chip } from "@/components/ui/chip";
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

function caretAfterChars(text: string, count: number): number {
  let seen = 0;
  for (let index = 0; index < text.length; index++) {
    if (seen === count) return index;
    if (text[index] !== " ") seen++;
  }
  return text.length;
}

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
    const pendingCaret = React.useRef<number | null>(null);

    const preferred = picked === undefined ? plateCountryIso(defaultCountry) : picked;
    const compact = normalizePlate(value);
    const country = plateCountryFor(compact, preferred, kind);
    const display = plateDisplay(compact, country, kind);
    // Старый свободный текст («самовывоз») показываем как записан.
    const text = /^[0-9A-Z]*$/.test(compact) ? display.text : value;
    const hint = text === display.text ? display.hint : "";
    const invalid = props["aria-invalid"] === true || props["aria-invalid"] === "true";
    // Номер с ошибкой форма объясняет сама — мягкое предупреждение её бы только дублировало.
    const softWarning = warning && !focused && !invalid ? plateWarning(compact, kind) : null;
    const lg = size === "lg";
    const textClass = lg
      ? "text-2xl font-bold tracking-wide tabular-nums"
      : "text-base font-medium tracking-wide tabular-nums sm:text-sm";

    React.useLayoutEffect(() => {
      const input = inputRef.current;
      if (pendingCaret.current === null || !input || document.activeElement !== input) return;
      const position = caretAfterChars(text, pendingCaret.current);
      pendingCaret.current = null;
      input.setSelectionRange(position, position);
    });

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
      <div
        className={cn(
          "flex w-full overflow-hidden rounded-md border border-[var(--input)] bg-[var(--background)] shadow-sm transition-[border-color,box-shadow] focus-within:border-[var(--ring)] focus-within:ring-2 focus-within:ring-[var(--ring)]/20",
          lg ? "h-14" : "h-10",
          invalid &&
            "border-[var(--destructive)] focus-within:border-[var(--destructive)] focus-within:ring-[var(--destructive)]/20",
          disabled && "cursor-not-allowed bg-[var(--muted)] opacity-70",
          className,
        )}
      >
        <div className="relative flex shrink-0 items-center gap-1.5 border-r border-[var(--input)] pl-3 pr-2 text-sm transition-colors hover:bg-[var(--accent)]">
          {country ? (
            <>
              <span aria-hidden className="text-base leading-none">
                {countryFlag(country)}
              </span>
              <span className="text-xs font-semibold">{country}</span>
            </>
          ) : (
            <Globe aria-hidden className="size-4 text-[var(--muted-foreground)]" />
          )}
          <ChevronDown aria-hidden className="size-3.5 text-[var(--muted-foreground)]" />
          <select
            aria-label={kind === "trailer" ? "Страна прицепа" : "Страна тягача"}
            value={country ?? ""}
            onChange={handleCountry}
            disabled={disabled}
            className="absolute inset-0 cursor-pointer opacity-0 disabled:cursor-not-allowed"
          >
            {PLATE_COUNTRIES.map((iso) => (
              <option key={iso} value={iso}>
                {countryFlag(iso)} {COUNTRY_NAMES[iso] ?? iso}
              </option>
            ))}
            <option value="">🌐 {OTHER_COUNTRY}</option>
          </select>
        </div>
        {/* 16px на телефоне: iOS не зумит страницу при фокусе. Подсказка того же размера, иначе съедет. */}
        <div className="relative min-w-0 flex-1">
          {hint && (
            <div
              aria-hidden
              className={cn(
                "pointer-events-none absolute inset-0 flex items-center overflow-hidden whitespace-pre px-3",
                textClass,
              )}
            >
              <span className="invisible">{text}</span>
              <span className="text-[var(--muted-foreground)]/50">{hint}</span>
            </div>
          )}
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
            className={cn(
              "relative h-full w-full bg-transparent px-3 outline-none disabled:cursor-not-allowed",
              textClass,
            )}
          />
        </div>
      </div>
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
