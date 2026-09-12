"use client";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Пилюля-переключатель фильтра (статус, период): `aria-pressed` отражает выбор. */
export function Chip({
  active,
  onClick,
  children,
  className,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium transition-colors",
        active
          ? "border-[var(--foreground)] bg-[var(--muted)]"
          : "border-[var(--border)] text-[var(--muted-foreground)] hover:border-[var(--foreground)]/40",
        className,
      )}
    >
      {children}
    </button>
  );
}
