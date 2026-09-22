"use client";

import { cn } from "@/lib/utils";

export interface SegmentedOption<T extends string> {
  value: T;
  label: React.ReactNode;
  caption?: React.ReactNode;
  disabled?: boolean;
}

/** Ряд взаимоисключающих кнопок (radiogroup): валюта, транспорт, статус. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  disabled,
  size = "md",
  className,
}: {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  disabled?: boolean;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn("grid gap-1.5", className)}
      style={{ gridTemplateColumns: `repeat(${options.length}, minmax(0, 1fr))` }}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled || option.disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "rounded-xl border px-2.5 text-left text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
              size === "sm" ? "min-h-9 py-1" : "min-h-10 py-1.5",
              active
                ? "border-slate-900 bg-slate-900 text-white shadow-sm"
                : "border-slate-200 bg-white text-slate-700 hover:border-slate-300",
            )}
          >
            <span className="block leading-tight">{option.label}</span>
            {option.caption && (
              <span
                className={cn("block truncate text-[11px] font-medium", active ? "text-white/70" : "text-slate-400")}
              >
                {option.caption}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
