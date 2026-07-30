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
        "relative flex min-h-32 flex-col gap-2 overflow-hidden rounded-[20px] border p-4 transition-all duration-200 sm:gap-3 sm:p-5",
        accent
          ? "border-[var(--ring)]/12 bg-[var(--soft-blue)]"
          : "border-[var(--border)] bg-[var(--card)] shadow-card hover:-translate-y-0.5 hover:border-[var(--ring)]/25",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="text-[12px] font-medium text-[var(--muted-foreground)]">{label}</span>
        {Icon && (
          <span
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-xl",
              accent ? "bg-[var(--card)]/75 text-[var(--ring)]" : "bg-[var(--muted)] text-[var(--muted-foreground)]",
            )}
          >
            <Icon className="size-4" />
          </span>
        )}
      </div>
      <div
        className={cn(
          "text-[24px] font-extrabold leading-[1.05] tracking-[-0.04em] tabular-nums sm:text-[32px]",
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
