import * as React from "react";
import { cn } from "@/lib/utils";

type Tone = "muted" | "primary" | "success" | "warning" | "destructive" | "outline";

const toneClasses: Record<Tone, string> = {
  muted: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  primary: "bg-[var(--soft-blue)] text-[var(--soft-blue-foreground)]",
  success: "bg-[var(--soft-green)] text-[var(--soft-green-foreground)]",
  warning: "bg-[var(--soft-amber)] text-[var(--soft-amber-foreground)]",
  destructive: "bg-[var(--soft-red)] text-[var(--soft-red-foreground)]",
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
        "inline-flex h-6 items-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 text-[11px] font-bold leading-none",
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
