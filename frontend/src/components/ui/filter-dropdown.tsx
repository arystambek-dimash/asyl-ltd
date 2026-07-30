"use client";
import { useEffect, useId, useRef, useState } from "react";
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
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(() => {
      const selected = listRef.current?.querySelector<HTMLButtonElement>('[aria-selected="true"]');
      const first = listRef.current?.querySelector<HTMLButtonElement>('[role="option"]');
      (selected ?? first)?.focus();
    });
  }, [open]);

  const current = options.find((o) => o.key === active) ?? options[0];
  const isDefault = current?.key === options[0]?.key;

  return (
    <div ref={ref} className="relative shrink-0">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(event) => {
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          setOpen(true);
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        className={cn(
          "flex h-11 items-center gap-1.5 rounded-xl border px-3 text-[12px] transition-all sm:h-10",
          isDefault
            ? "border-[var(--input)] bg-[var(--card)] text-[var(--muted-foreground)] shadow-sm hover:border-[var(--muted-foreground)]/35 hover:text-[var(--foreground)]"
            : "border-[var(--ring)]/30 bg-[var(--soft-blue)] text-[var(--foreground)]",
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
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={label}
          onKeyDown={(event) => {
            const options = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? []);
            const currentIndex = options.indexOf(document.activeElement as HTMLButtonElement);
            if (event.key === "Escape" || event.key === "Tab") {
              if (event.key === "Escape") event.preventDefault();
              setOpen(false);
              triggerRef.current?.focus();
              return;
            }
            let nextIndex: number | null = null;
            if (event.key === "ArrowDown") nextIndex = (currentIndex + 1) % options.length;
            if (event.key === "ArrowUp") nextIndex = (currentIndex - 1 + options.length) % options.length;
            if (event.key === "Home") nextIndex = 0;
            if (event.key === "End") nextIndex = options.length - 1;
            if (nextIndex === null || !options.length) return;
            event.preventDefault();
            options[nextIndex]?.focus();
          }}
          className="absolute left-0 z-40 mt-2 max-h-72 min-w-[220px] overflow-y-auto rounded-xl border bg-[var(--card)] p-1.5 shadow-float animate-modal-content"
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
                  triggerRef.current?.focus();
                }}
                className={cn(
                  "flex min-h-11 w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] transition-colors sm:min-h-10",
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
