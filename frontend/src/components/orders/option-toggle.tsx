"use client";

import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const TONES = {
  amber: {
    on: "border-amber-300 bg-amber-50/40",
    icon: "bg-amber-100 text-amber-700",
    track: "bg-amber-500",
    divider: "border-amber-200/70",
  },
  emerald: {
    on: "border-emerald-300 bg-emerald-50/40",
    icon: "bg-emerald-100 text-emerald-700",
    track: "bg-emerald-500",
    divider: "border-emerald-200/70",
  },
} as const;

/** Необязательная опция формы заказа: карточка с переключателем, поля раскрываются под ней. */
export function OptionToggle({
  checked,
  onChange,
  icon: Icon,
  title,
  caption,
  ariaLabel,
  tone,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  icon: LucideIcon;
  title: string;
  caption: string;
  ariaLabel: string;
  tone: keyof typeof TONES;
  children?: React.ReactNode;
}) {
  const colors = TONES[tone];
  return (
    <div className={cn("rounded-xl border transition", checked ? colors.on : "border-slate-200 bg-white")}>
      <label className="flex cursor-pointer items-center gap-3 px-4 py-3">
        <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg", colors.icon)}>
          <Icon className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-slate-900">{title}</span>
          <span className="block text-xs text-slate-500">{caption}</span>
        </span>
        <span
          aria-hidden="true"
          className={cn("relative h-6 w-11 shrink-0 rounded-full transition", checked ? colors.track : "bg-slate-300")}
        >
          <span
            className={cn(
              "absolute top-0.5 size-5 rounded-full bg-white shadow transition",
              checked ? "left-[22px]" : "left-0.5",
            )}
          />
        </span>
        <input
          type="checkbox"
          className="sr-only"
          aria-label={ariaLabel}
          checked={checked}
          onChange={(event) => onChange(event.target.checked)}
        />
      </label>
      {checked && children && <div className={cn("border-t px-4 py-4", colors.divider)}>{children}</div>}
    </div>
  );
}
