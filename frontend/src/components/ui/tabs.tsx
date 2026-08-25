"use client";
import type { KeyboardEvent } from "react";
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
  label = "Разделы",
}: {
  tabs: TabDef[];
  active: string;
  onChange: (key: string) => void;
  variant?: "underline" | "segment";
  className?: string;
  label?: string;
}) {
  function moveFocus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex === null || !tabs[nextIndex]) return;

    event.preventDefault();
    onChange(tabs[nextIndex].key);
    event.currentTarget
      .closest<HTMLElement>('[role="tablist"]')
      ?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
      [nextIndex]?.focus();
  }

  const segment = variant === "segment";
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn(
        segment
          ? "inline-flex rounded-md border border-[var(--border)] bg-[var(--muted)] p-0.5"
          : "flex gap-6 border-b border-[var(--border)]",
        className,
      )}
    >
      {tabs.map((t, index) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={active === t.key}
          aria-label={t.count === undefined ? undefined : `${t.label}, ${t.count}`}
          tabIndex={active === t.key ? 0 : -1}
          onClick={() => onChange(t.key)}
          onKeyDown={(event) => moveFocus(event, index)}
          className={cn(
            segment
              ? "inline-flex h-8 items-center gap-1.5 rounded px-4 text-sm transition-colors"
              : "-mb-px inline-flex h-11 items-center gap-2 border-b-2 px-1 text-[15px] transition-colors",
            segment && active === t.key
              ? "bg-[var(--card)] font-medium text-[var(--foreground)] shadow-sm"
              : segment
                ? "text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                : active === t.key
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
