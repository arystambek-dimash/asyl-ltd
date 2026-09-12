"use client";
import { Delete } from "lucide-react";
import { cn } from "@/lib/utils";

const DIGITS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;
const KEY =
  "flex h-14 items-center justify-center rounded-xl text-[26px] font-medium tabular-nums transition-colors " +
  "focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50 disabled:opacity-40";
const DIGIT_KEY = cn(
  KEY,
  "bg-[var(--muted)] text-[var(--foreground)] hover:bg-[var(--border)] active:bg-[var(--border)]",
);

/** Цифровая клавиатура суммы, как в Kaspi POS: 1–9, 0 и «стереть». */
export function Numpad({
  onDigit,
  onBackspace,
  disabled = false,
  className,
}: {
  onDigit: (digit: string) => void;
  onBackspace: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("grid grid-cols-3 gap-2", className)}>
      {DIGITS.map((digit) => (
        <button
          key={digit}
          type="button"
          aria-label={`Цифра ${digit}`}
          disabled={disabled}
          onClick={() => onDigit(digit)}
          className={DIGIT_KEY}
        >
          {digit}
        </button>
      ))}
      <span aria-hidden />
      <button type="button" aria-label="Цифра 0" disabled={disabled} onClick={() => onDigit("0")} className={DIGIT_KEY}>
        0
      </button>
      <button
        type="button"
        aria-label="Стереть"
        disabled={disabled}
        onClick={onBackspace}
        className={cn(KEY, "bg-[var(--secondary)] text-[var(--foreground)] hover:bg-[var(--border)]")}
      >
        <Delete className="size-6" aria-hidden />
      </button>
    </div>
  );
}
