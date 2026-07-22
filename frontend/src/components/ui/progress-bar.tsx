import * as React from "react";
import { cn } from "@/lib/utils";

export function ProgressBar({ pct, className }: { pct: number; className?: string }) {
  const clamped = Math.max(0, Math.min(pct, 100));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped}
      aria-valuetext={`${Math.round(clamped)}%`}
      className={cn("h-1 w-full overflow-hidden rounded-full bg-[var(--muted)]", className)}
    >
      <div
        className={cn(
          "h-full rounded-full transition-all",
          clamped >= 100 ? "bg-[var(--success)]" : "bg-[var(--ring)]",
        )}
        style={{ width: `${clamped}%` }}
      />
    </div>
  );
}
