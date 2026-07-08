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
    // Группа скроллится по горизонтали (без видимого скроллбара),
    // ярлыки не переносятся; в flex-строке группа умеет ужиматься.
    <div className="inline-flex max-w-full overflow-x-auto rounded-md border border-[var(--border)] bg-[var(--muted)] p-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {items.map((it) => {
        const on = it.key === active;
        return (
          <button
            key={it.key}
            type="button"
            onClick={() => onChange(it.key)}
            className={cn(
              "h-7 shrink-0 whitespace-nowrap px-2.5 inline-flex items-center gap-1.5 text-[13px] rounded transition-colors",
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
