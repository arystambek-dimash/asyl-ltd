import * as React from "react";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  accent,
  tone,
  caption,
  icon: Icon,
  className,
  children,
}: {
  label: string;
  value: React.ReactNode;
  accent?: boolean;
  /** Цвет значения: красный для долгов/просрочки, зелёный для оплат. */
  tone?: "destructive" | "success";
  caption?: string;
  icon?: React.ElementType;
  className?: string;
  /** Дополнительное содержимое под подписью (например, бар распределения). */
  children?: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-2 sm:gap-3 rounded-lg border p-3 sm:p-5 transition-colors",
        accent
          ? "border-[var(--ring)]/20 bg-[var(--ring)]/10"
          : "border-[var(--border)] bg-[var(--card)] hover:border-[var(--ring)]/40",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-[12px] font-medium text-[var(--muted-foreground)]">{label}</span>
        {Icon && (
          <span
            className={cn(
              "flex size-7 shrink-0 items-center justify-center rounded-md",
              accent ? "bg-[var(--ring)]/15 text-[var(--ring)]" : "bg-[var(--muted)] text-[var(--muted-foreground)]",
            )}
          >
            <Icon className="size-4" />
          </span>
        )}
      </div>
      <div
        className={cn(
          "text-[20px] sm:text-[30px] leading-[1.1] tracking-tight tabular-nums font-semibold",
          accent ? "text-[var(--ring)]" : "text-[var(--foreground)]",
          tone === "destructive" && "text-[var(--destructive)]",
          tone === "success" && "text-[var(--success)]",
        )}
      >
        {value}
      </div>
      {caption && <span className="text-[12px] text-[var(--muted-foreground)]">{caption}</span>}
      {children}
    </div>
  );
}
