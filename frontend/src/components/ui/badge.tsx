import * as React from "react";
import { cn } from "@/lib/utils";

type Tone = "muted" | "primary" | "success" | "warning" | "destructive" | "outline";

const toneClasses: Record<Tone, string> = {
  muted: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  primary: "bg-[var(--ring)]/12 text-[var(--ring)]",
  success: "bg-[var(--success)]/12 text-[var(--success)]",
  warning: "bg-[var(--warning)]/15 text-[var(--warning)]",
  destructive: "bg-[var(--destructive)]/12 text-[var(--destructive)]",
  outline: "bg-transparent text-[var(--muted-foreground)] border border-[var(--border)]",
};

const dotColor: Record<Tone, string> = {
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
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone; dot?: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 h-[22px] text-[12px] rounded-md font-medium leading-none whitespace-nowrap",
        toneClasses[tone],
        className,
      )}
      {...props}
    >
      {dot && <span className="h-1.5 w-1.5 rounded-full" style={{ background: dotColor[tone] }} />}
      {children}
    </span>
  );
}
