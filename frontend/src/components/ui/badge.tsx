import * as React from "react";
import type { BadgeTone } from "@/lib/constants";
import { cn } from "@/lib/utils";

const toneClasses: Record<BadgeTone, string> = {
  muted: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  primary: "bg-[var(--ring)]/12 text-[var(--ring)]",
  success: "bg-[var(--success)]/12 text-[var(--success)]",
  warning: "bg-[var(--warning)]/15 text-[var(--warning)]",
  destructive: "bg-[var(--destructive)]/12 text-[var(--destructive)]",
  outline: "bg-transparent text-[var(--muted-foreground)] border border-[var(--border)]",
};

/** CSS-цвет тона — для точек и полос вне Badge. */
export const BADGE_TONE_COLOR: Record<BadgeTone, string> = {
  muted: "var(--muted-foreground)",
  primary: "var(--ring)",
  success: "var(--success)",
  warning: "var(--warning)",
  destructive: "var(--destructive)",
  outline: "var(--muted-foreground)",
};

export function Badge({
  tone = "muted",
  dot,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: BadgeTone; dot?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 h-[22px] text-[12px] rounded-md font-medium leading-none whitespace-nowrap",
        toneClasses[tone],
        className,
      )}
      {...props}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full" style={{ background: BADGE_TONE_COLOR[tone] }} />}
      {children}
    </span>
  );
}
