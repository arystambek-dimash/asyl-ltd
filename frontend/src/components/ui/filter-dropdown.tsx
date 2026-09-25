"use client";
import { useEffect, useId, useRef, useState, type ComponentProps, type ElementType, type KeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";
import { nextRovingIndex } from "@/lib/focus";
import { useDismiss } from "@/lib/use-dismiss";
import { cn } from "@/lib/utils";

export type FilterOption = { key: string; label: string; count?: number };

/**
 * Кнопка фильтра «Ярлык: Значение ▾». active — выбран не вариант по
 * умолчанию: кнопка подсвечивается, чтобы было видно, что список сужен.
 */
export function FilterTrigger({
  label,
  value,
  count,
  active,
  open,
  icon: Icon,
  className,
  ...props
}: Omit<ComponentProps<"button">, "value"> & {
  label: string;
  value: string;
  count?: number;
  active: boolean;
  open: boolean;
  icon?: ElementType;
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      {...props}
      className={cn(
        "flex h-9 items-center gap-1.5 rounded-md border px-3 text-[13px] transition-colors",
        active
          ? "border-[var(--primary)]/40 bg-[var(--primary)]/5 text-[var(--foreground)]"
          : "border-[var(--border)] bg-[var(--card)] text-[var(--muted-foreground)] hover:text-[var(--foreground)]",
        className,
      )}
    >
      {Icon && <Icon className="size-3.5" />}
      <span className="text-[var(--muted-foreground)]">{label}:</span>
      <span className="max-w-52 truncate font-medium">{value}</span>
      {count !== undefined && <span className="tabular-nums text-[11px] text-[var(--muted-foreground)]">{count}</span>}
      <ChevronDown
        className={cn("size-3.5 text-[var(--muted-foreground)] transition-transform", open && "rotate-180")}
      />
    </button>
  );
}

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
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const focusIndexRef = useRef(0);
  const listboxId = useId();

  useDismiss(ref, () => setOpen(false), open, { returnFocusRef: triggerRef });

  const current = options.find((o) => o.key === active) ?? options[0];
  const currentIndex = Math.max(
    0,
    options.findIndex((option) => option.key === current?.key),
  );
  const isDefault = current?.key === options[0]?.key;

  useEffect(() => {
    if (open) optionRefs.current[focusIndexRef.current]?.focus();
  }, [open]);

  function openAt(index: number) {
    focusIndexRef.current = index;
    setOpen(true);
  }

  function selectOption(index: number) {
    const option = options[index];
    if (!option) return;
    onChange(option.key);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    openAt(event.key === "ArrowUp" ? options.length - 1 : currentIndex);
  }

  function onListboxKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const focusedIndex = optionRefs.current.findIndex((option) => option === document.activeElement);
    const nextIndex = nextRovingIndex(event.key, focusedIndex, options.length);
    if (nextIndex !== null) {
      event.preventDefault();
      focusIndexRef.current = nextIndex;
      optionRefs.current[nextIndex]?.focus();
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div ref={ref} className="relative shrink-0">
      <FilterTrigger
        ref={triggerRef}
        label={label}
        value={current?.label ?? "—"}
        count={current?.count}
        active={!isDefault}
        open={open}
        onClick={() => (open ? setOpen(false) : openAt(currentIndex))}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="listbox"
        aria-controls={open ? listboxId : undefined}
        disabled={options.length === 0}
      />

      {open && (
        <div
          id={listboxId}
          role="listbox"
          aria-label={label}
          onKeyDown={onListboxKeyDown}
          className="absolute left-0 z-40 mt-1 max-h-72 min-w-[210px] overflow-y-auto rounded-lg border bg-[var(--card)] p-1 shadow-lg"
        >
          {options.map((o, index) => {
            const on = o.key === active;
            return (
              <button
                key={o.key}
                ref={(node) => {
                  optionRefs.current[index] = node;
                }}
                type="button"
                role="option"
                aria-selected={on}
                aria-label={o.count === undefined ? undefined : `${o.label}, ${o.count}`}
                tabIndex={-1}
                onClick={() => selectOption(index)}
                className={cn(
                  "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[13px] outline-none transition-colors focus:bg-[var(--muted)]",
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
