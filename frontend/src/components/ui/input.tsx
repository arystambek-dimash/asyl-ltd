import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      className={cn(
        "flex h-11 w-full rounded-xl border border-[var(--input)] bg-[var(--card)] px-3.5 py-2 text-sm shadow-[0_2px_8px_-7px_rgba(23,32,27,.5)] outline-none transition-[border-color,box-shadow,background-color] placeholder:text-[var(--muted-foreground)]/65 focus-visible:border-[var(--ring)] focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/14 disabled:cursor-not-allowed disabled:bg-[var(--muted)] disabled:opacity-70",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
