"use client";
import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export type FilterOption = { key: string; label: string; count?: number };

/**
 * Компактный фильтр-дропдаун: кнопка «Ярлык: Значение ▾» и меню с вариантами
 * и счётчиками. Заменяет ряды пилюль, которые не влезали на телефоне.
 */
export function FilterDropdown({
  label,
  options,
  active,
  onChange,
}: {
  label: string;
  options: FilterOption[];
  active: string;
  onChange: (key: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const current = options.find((o) => o.key === active) ?? options[0];
  const isDefault = current?.key === options[0]?.key;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "flex h-9 items-center gap-1.5 rounded-md border px-3 text-[13px] transition-colors",
          isDefault
            ? "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
            : "border-[var(--primary)]/40 bg-[var(--primary)]/5 text-[var(--foreground)]",
        )}
      >
        <span className="text-[var(--muted-foreground)]">{label}:</span>
        <span className="font-medium">{current?.label ?? "—"}</span>
        {current?.count !== undefined && (
          <span className="tabular-nums text-[11px] text-[var(--muted-foreground)]">{current.count}</span>
        )}
        <ChevronDown
          className={cn("size-3.5 text-[var(--muted-foreground)] transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute left-0 z-40 mt-1 max-h-72 min-w-[210px] overflow-y-auto rounded-lg border bg-[var(--card)] p-1 shadow-lg"
        >
          {options.map((o) => {
            const on = o.key === active;
            return (
              <button
                key={o.key}
                type="button"
                role="option"
                aria-selected={on}
                onClick={() => {
                  onChange(o.key);
                  setOpen(false);
                }}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[13px] transition-colors",
                  on ? "bg-[var(--muted)] font-medium" : "hover:bg-[var(--muted)]/60",
                )}
              >
                <span className="flex-1 truncate">{o.label}</span>
                {o.count !== undefined && (
                  <span className="tabular-nums text-[11px] text-[var(--muted-foreground)]">{o.count}</span>
                )}
                {on && <Check className="size-3.5 text-[var(--primary)]" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
