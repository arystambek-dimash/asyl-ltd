"use client";
import { cn } from "@/lib/utils";

export interface TabDef {
  key: string;
  label: string;
  icon?: React.ElementType;
}

/** Переключатель вкладок.
 * variant="segment" — компактный сегмент (в углу/actions).
 * variant="bar" — широкая полоса-панель во всю ширину под шапкой (как на «Главной»). */
export function Tabs({ tabs, active, onChange, variant = "segment", className }: {
  tabs: TabDef[];
  active: string;
  onChange: (key: string) => void;
  variant?: "segment" | "bar";
  className?: string;
}) {
  if (variant === "bar") {
    return (
      <div className={cn(
        "flex gap-1 rounded-xl border border-[var(--border)] bg-[var(--card)] p-1 shadow-card",
        className,
      )}>
        {tabs.map((t) => (
          <button key={t.key} type="button" onClick={() => onChange(t.key)}
            className={cn(
              "inline-flex h-10 items-center gap-2 rounded-lg px-4 text-sm transition-colors",
              active === t.key
                ? "bg-[var(--muted)] font-semibold text-[var(--foreground)]"
                : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/60 hover:text-[var(--foreground)]",
            )}>
            {t.icon && <t.icon className="size-4" />}
            {t.label}
          </button>
        ))}
      </div>
    );
  }
  return (
    <div className={cn(
      "inline-flex rounded-md border border-[var(--border)] bg-[var(--muted)] p-0.5",
      className,
    )}>
      {tabs.map((t) => (
        <button key={t.key} type="button" onClick={() => onChange(t.key)}
          className={cn(
            "inline-flex h-8 items-center gap-1.5 rounded px-4 text-sm transition-colors",
            active === t.key
              ? "bg-[var(--card)] font-medium text-[var(--foreground)] shadow-sm"
              : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
          )}>
          {t.icon && <t.icon className="size-4" />}
          {t.label}
        </button>
      ))}
    </div>
  );
}
