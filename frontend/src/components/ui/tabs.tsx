"use client";
import { useRef } from "react";
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
        "inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[10px] font-bold tabular-nums",
        active
          ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
          : "bg-[var(--muted)] text-[var(--muted-foreground)]",
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
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  function handleKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    let nextIndex: number | null = null;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabs.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    onChange(tabs[nextIndex].key);
    tabRefs.current[nextIndex]?.focus();
  }

  if (variant === "segment") {
    return (
      <div
        className={cn(
          "no-scrollbar inline-flex max-w-full overflow-x-auto rounded-xl border border-[var(--border)] bg-[var(--muted)] p-1",
          className,
        )}
        role="tablist"
        aria-label="Разделы страницы"
      >
        {tabs.map((t, index) => (
          <button
            key={t.key}
            ref={(node) => {
              tabRefs.current[index] = node;
            }}
            type="button"
            onClick={() => onChange(t.key)}
            onKeyDown={(event) => handleKeyDown(event, index)}
            role="tab"
            aria-selected={active === t.key}
            tabIndex={active === t.key ? 0 : -1}
            className={cn(
              "inline-flex h-10 shrink-0 items-center gap-1.5 rounded-lg px-4 text-sm font-medium transition-all",
              active === t.key
                ? "bg-[var(--card)] font-semibold text-[var(--foreground)] shadow-sm"
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
    <div
      className={cn("no-scrollbar flex max-w-full gap-5 overflow-x-auto border-b border-[var(--border)]", className)}
      role="tablist"
      aria-label="Разделы страницы"
    >
      {tabs.map((t, index) => (
        <button
          key={t.key}
          ref={(node) => {
            tabRefs.current[index] = node;
          }}
          type="button"
          onClick={() => onChange(t.key)}
          onKeyDown={(event) => handleKeyDown(event, index)}
          role="tab"
          aria-selected={active === t.key}
          tabIndex={active === t.key ? 0 : -1}
          className={cn(
            "-mb-px inline-flex h-12 shrink-0 items-center gap-2 border-b-2 px-1 text-[14px] transition-colors",
            active === t.key
              ? "border-[var(--ring)] font-bold text-[var(--foreground)]"
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
