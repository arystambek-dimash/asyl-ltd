"use client";
import { useRef } from "react";
import { Label } from "@/components/ui/label";
import { PlateInput } from "@/components/ui/plate-input";
import { WagonNumberInput } from "@/components/ui/wagon-number-input";
import type { TransportPair } from "@/lib/plates";
import type { Order } from "@/lib/types";
import { cn, PHONE_INPUT_TEXT } from "@/lib/utils";

/** Ошибка поля: текст — показать под полем; true — только подсветить поле. */
type FieldProblem = string | boolean | null | undefined;

/**
 * Номер транспорта заказа: у вагона — 8 цифр, у машины — тягач и прицеп.
 * У вагона номер лежит в ``truck_number``; оформление (пробелы, дефисы) отбрасывается
 * при вводе — см. WagonNumberInput. Enter в поле тягача переводит к прицепу.
 */
export function TransportNumberFields({
  id,
  transportType,
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
  return (
    <div className={cn("grid gap-3", className)}>
      <div>
        <Label htmlFor={`${id}-truck`} className={labelClassName}>
          Тягач
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
      </div>
      <div>
        <Label htmlFor={`${id}-trailer`} className={labelClassName}>
          Прицеп (необязательно)
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
    </div>
  );
}
