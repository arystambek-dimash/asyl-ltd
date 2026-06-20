import * as React from "react";
import { cn } from "@/lib/utils";

type Tone = "muted" | "primary" | "success" | "warning" | "destructive" | "outline";

const toneClasses: Record<Tone, string> = {
  muted: "bg-[var(--muted)] text-[var(--muted-foreground)]",
  primary: "bg-[var(--primary)]/10 text-[var(--primary)] border border-[var(--primary)]/30",
  success: "bg-[var(--success)]/12 text-[var(--success)] border border-[var(--success)]/30",
  warning: "bg-[var(--warning)]/15 text-[var(--warning)] border border-[var(--warning)]/30",
  destructive: "bg-[var(--destructive)]/10 text-[var(--destructive)] border border-[var(--destructive)]/30",
  outline: "border text-[var(--foreground)]",
};

export function Badge({
  tone = "muted",
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        toneClasses[tone],
        className
      )}
      {...props}
    />
  );
}
