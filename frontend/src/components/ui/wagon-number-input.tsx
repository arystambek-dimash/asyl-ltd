"use client";
import * as React from "react";
import { Input } from "@/components/ui/input";
import { typedWagonNumber } from "@/lib/plates";
import { cn } from "@/lib/utils";

type WagonNumberInputProps = Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & {
  value: string;
  onChange: (value: string) => void;
  /** Почему номер не примут — выводится под полем. */
  error?: string | null;
};

/** Номер вагона: 8 цифр без оформления, ведущие нули сохраняются; ошибка — под полем. */
export function WagonNumberInput({ value, onChange, error, id, className, ...props }: WagonNumberInputProps) {
  const fallbackId = React.useId();
  const errorId = `${id ?? fallbackId}-error`;
  return (
    <div>
      <Input
        id={id}
        inputMode="numeric"
        placeholder="8 цифр"
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        {...props}
        value={value}
        onChange={(event) => onChange(typedWagonNumber(event.target.value))}
        className={cn("tabular-nums", className)}
      />
      {error && (
        <p id={errorId} className="mt-1 text-xs text-[var(--destructive)]">
          {error}
        </p>
      )}
    </div>
  );
}
