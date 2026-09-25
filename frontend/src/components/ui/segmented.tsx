"use client";

import { cn } from "@/lib/utils";

interface SegmentedOption<T extends string> {
  value: T;
  label: React.ReactNode;
  caption?: React.ReactNode;
}

/** Ряд взаимоисключающих кнопок (radiogroup): валюта, транспорт, статус. */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  disabled,
}: {
  value: T;
  options: SegmentedOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  disabled?: boolean;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className="grid gap-1.5"
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
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "min-h-10 rounded-xl border px-2.5 py-1.5 text-left text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
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

/**
 * Компактный переключатель-пилюля на серой подложке (кнопки с aria-pressed):
 * вид данных, период, режим окна. sm — мелкий в строке заголовка, md — обычный.
 */
export function PillToggle<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  size = "md",
  disabled,
  className,
}: {
  /** Активная опция; значение не из options — ни одна не нажата. */
  value: T | null;
  options: SegmentedOption<T>[];
  onChange: (value: T) => void;
  ariaLabel: string;
  size?: "sm" | "md";
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex flex-wrap rounded-lg bg-[var(--muted)]",
        size === "sm" ? "gap-0.5 p-0.5" : "gap-1 p-1",
        className,
      )}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex items-center justify-center gap-2 rounded-md transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)] disabled:cursor-not-allowed disabled:opacity-50",
              size === "sm" ? "px-2.5 py-1 text-[11px] font-semibold" : "min-h-10 px-3 text-sm font-medium",
              active
                ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
