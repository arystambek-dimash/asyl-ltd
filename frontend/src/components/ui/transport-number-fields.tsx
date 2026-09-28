"use client";
import { useRef, useState } from "react";
import { Label } from "@/components/ui/label";
import { PlateInput } from "@/components/ui/plate-input";
import { WagonNumberInput } from "@/components/ui/wagon-number-input";
import type { TransportPair } from "@/lib/plates";
import type { Order } from "@/lib/types";
import { cn, PHONE_INPUT_TEXT } from "@/lib/utils";

/** Ошибка поля: текст — показать под полем; true — только подсветить поле. */
type FieldProblem = string | boolean | null | undefined;

/** Вид машины — только в интерфейсе: для API и газель, и фура — «truck». */
export type TruckKind = "fura" | "gazelle";

/**
 * Номер транспорта заказа: у вагона — 8 цифр, у фуры — тягач и прицеп, у газели —
 * один номер машины и прицеп по галочке «Есть прицеп».
 * У вагона номер лежит в ``truck_number``; оформление (пробелы, дефисы) отбрасывается
 * при вводе — см. WagonNumberInput. Enter в поле тягача переводит к прицепу.
 */
export function TransportNumberFields({
  id,
  transportType,
  truckKind = "fura",
  value,
  onChange,
  defaultCountry,
  disabled,
  size = "md",
  errors,
  hint,
  labelClassName,
  className,
}: {
  /** Префикс id полей: «<id>-truck», «<id>-trailer», «<id>-wagon». */
  id: string;
  transportType: Order["transport_type"];
  truckKind?: TruckKind;
  value: TransportPair;
  onChange: (value: TransportPair) => void;
  /** Страна клиента — маска пустого номера машины. */
  defaultCountry?: string;
  disabled?: boolean;
  /** lg — крупные поля экрана грузчика. */
  size?: "md" | "lg";
  /** У вагона ошибка номера — в ``truck``. */
  errors?: { truck?: FieldProblem; trailer?: FieldProblem };
  /** Подсказка под полями. */
  hint?: string;
  labelClassName?: string;
  className?: string;
}) {
  const trailerRef = useRef<HTMLInputElement>(null);
  const [trailerChecked, setTrailerChecked] = useState(false);
  const problem = (field: "truck" | "trailer") => {
    const message = errors?.[field];
    const errorId = `${id}-${field}-error`;
    return {
      invalid: message ? true : undefined,
      describedBy: typeof message === "string" && message ? errorId : undefined,
      error:
        typeof message === "string" && message ? (
          <p id={errorId} className="mt-1 text-xs text-[var(--destructive)]">
            {message}
          </p>
        ) : null,
    };
  };
  const truck = problem("truck");
  const hintLine = hint ? <p className="mt-1.5 text-[11px] text-[var(--muted-foreground)]">{hint}</p> : null;

  if (transportType === "train") {
    return (
      <div className={className}>
        <Label htmlFor={`${id}-wagon`} className={labelClassName}>
          Номер вагона
        </Label>
        <WagonNumberInput
          id={`${id}-wagon`}
          disabled={disabled}
          value={value.truck_number}
          error={typeof errors?.truck === "string" ? errors.truck : null}
          aria-invalid={truck.invalid}
          onChange={(truck_number) => onChange({ ...value, truck_number })}
          className={size === "lg" ? "h-14 text-2xl font-bold tracking-wide" : PHONE_INPUT_TEXT}
        />
        {hintLine}
      </div>
    );
  }

  const trailer = problem("trailer");
  const gazelle = truckKind === "gazelle";
  // У газели прицеп — по галочке; записанный номер прицепа держит её включённой.
  const withTrailer = !gazelle || trailerChecked || value.trailer_number !== "";
  return (
    <div className={cn("grid gap-3", className)}>
      <div>
        <Label htmlFor={`${id}-truck`} className={labelClassName}>
          {gazelle ? "Номер машины" : "Тягач"}
        </Label>
        <PlateInput
          id={`${id}-truck`}
          size={size}
          warning
          enterKeyHint="next"
          defaultCountry={defaultCountry}
          disabled={disabled}
          aria-invalid={truck.invalid}
          aria-describedby={truck.describedBy}
          value={value.truck_number}
          onChange={(truck_number) => onChange({ ...value, truck_number })}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            trailerRef.current?.focus();
          }}
        />
        {truck.error}
        {gazelle && (
          <label className="mt-2 flex w-fit cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              className="size-4 accent-[var(--primary)]"
              checked={withTrailer}
              disabled={disabled}
              onChange={(event) => {
                setTrailerChecked(event.target.checked);
                if (!event.target.checked) onChange({ ...value, trailer_number: "" });
              }}
            />
            Есть прицеп
          </label>
        )}
        {!withTrailer && hintLine}
      </div>
      {withTrailer && (
        <div>
          <Label htmlFor={`${id}-trailer`} className={labelClassName}>
            {gazelle ? "Прицеп" : "Прицеп (необязательно)"}
          </Label>
          <PlateInput
            ref={trailerRef}
            id={`${id}-trailer`}
            kind="trailer"
            size={size}
            defaultCountry={defaultCountry}
            disabled={disabled}
            aria-invalid={trailer.invalid}
            aria-describedby={trailer.describedBy}
            value={value.trailer_number}
            onChange={(trailer_number) => onChange({ ...value, trailer_number })}
          />
          {trailer.error}
          {hintLine}
        </div>
      )}
    </div>
  );
}
