"use client";
import { cn } from "@/lib/utils";

export interface TabDef {
  key: string;
  label: string;
  icon?: React.ElementType;
}

/** Сегментированный переключатель вкладок (единый вид с дашбордом). */
export function Tabs({ tabs, active, onChange }: {
  tabs: TabDef[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-[var(--border)] bg-[var(--muted)] p-0.5">
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
