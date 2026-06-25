import * as React from "react";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  accent,
  caption,
  icon: Icon,
}: {
  label: string;
  value: React.ReactNode;
  accent?: boolean;
  caption?: string;
  icon?: React.ElementType;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-lg border p-4 sm:p-5 transition-colors",
        accent
          ? "border-[var(--ring)]/20 bg-[var(--ring)]/10"
          : "border-[var(--border)] bg-[var(--card)] hover:border-[var(--ring)]/40"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-[12px] font-medium text-[var(--muted-foreground)]">{label}</span>
        {Icon && (
          <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-md",
            accent ? "bg-[var(--ring)]/15 text-[var(--ring)]" : "bg-[var(--muted)] text-[var(--muted-foreground)]")}>
            <Icon className="size-4" />
          </span>
        )}
      </div>
      <div
        className={cn(
          "text-[24px] sm:text-[30px] leading-[1.1] tracking-tight tabular-nums font-semibold",
          accent ? "text-[var(--ring)]" : "text-[var(--foreground)]"
        )}
      >
        {value}
      </div>
      {caption && <span className="text-[12px] text-[var(--muted-foreground)]">{caption}</span>}
    </div>
  );
}
