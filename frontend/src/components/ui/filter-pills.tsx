import * as React from "react";
import { cn } from "@/lib/utils";

export type FilterPillItem = { key: string; label: string; count: number };

export function FilterPills({
  items,
  active,
  onChange,
}: {
  items: FilterPillItem[];
  active: string;
  onChange: (key: string) => void;
}) {
  return (
    <div className="inline-flex bg-[var(--muted)] border border-[var(--border)] rounded-md p-0.5">
      {items.map((it) => {
        const on = it.key === active;
        return (
          <button
            key={it.key}
            type="button"
            onClick={() => onChange(it.key)}
            className={cn(
              "h-7 px-2.5 inline-flex items-center gap-1.5 text-[13px] rounded transition-colors",
              on
                ? "bg-[var(--card)] text-[var(--foreground)] shadow-sm font-medium"
                : "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            )}
          >
            {it.label}
            <span
              className={cn(
                "text-[11px] tabular-nums",
                on ? "text-[var(--muted-foreground)]" : "text-[var(--muted-foreground)]/70"
              )}
            >
              {it.count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
