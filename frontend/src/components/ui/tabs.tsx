"use client";
import { cn } from "@/lib/utils";

export interface TabDef {
  key: string;
  label: string;
  icon?: React.ElementType;
  /** Счётчик в пилюле рядом с названием (0 тоже показывается). */
  count?: number;
}

function TabCount({ value, active }: { value: number; active: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold tabular-nums",
        active ? "bg-[var(--foreground)] text-[var(--background)]" : "bg-[var(--muted)] text-[var(--muted-foreground)]",
      )}
    >
      {value}
    </span>
  );
}

/** Переключатель вкладок.
 * variant="underline" (по умолчанию) — навбарная полоса: текст с подчёркиванием
 * активной вкладки, как в кассовых отчётах.
 * variant="segment" — компактный сегмент (в углу/actions). */
export function Tabs({
  tabs,
  active,
  onChange,
  variant = "underline",
  className,
}: {
  tabs: TabDef[];
  active: string;
  onChange: (key: string) => void;
  variant?: "underline" | "segment";
  className?: string;
}) {
  if (variant === "segment") {
    return (
      <div className={cn("inline-flex rounded-md border border-[var(--border)] bg-[var(--muted)] p-0.5", className)}>
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            className={cn(
              "inline-flex h-8 items-center gap-1.5 rounded px-4 text-sm transition-colors",
              active === t.key
                ? "bg-[var(--card)] font-medium text-[var(--foreground)] shadow-sm"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
            )}
          >
            {t.icon && <t.icon className="size-4" />}
            {t.label}
            {t.count !== undefined && <TabCount value={t.count} active={active === t.key} />}
          </button>
        ))}
      </div>
    );
  }
  return (
    <div className={cn("flex gap-6 border-b border-[var(--border)]", className)}>
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          onClick={() => onChange(t.key)}
          className={cn(
            "-mb-px inline-flex h-11 items-center gap-2 border-b-2 px-1 text-[15px] transition-colors",
            active === t.key
              ? "border-[var(--foreground)] font-semibold text-[var(--foreground)]"
              : "border-transparent text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
          )}
        >
          {t.icon && <t.icon className="size-4" />}
          {t.label}
          {t.count !== undefined && <TabCount value={t.count} active={active === t.key} />}
        </button>
      ))}
    </div>
  );
}
