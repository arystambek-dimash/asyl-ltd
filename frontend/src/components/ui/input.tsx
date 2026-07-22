import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => (
    <input
      type={type}
      ref={ref}
      className={cn(
        "flex h-10 w-full rounded-md border border-[var(--input)] bg-[var(--background)] px-3.5 py-2 text-sm shadow-sm outline-none transition-[border-color,box-shadow,background-color] placeholder:text-[var(--muted-foreground)]/70 focus-visible:border-[var(--ring)] focus-visible:ring-2 focus-visible:ring-[var(--ring)]/20 disabled:cursor-not-allowed disabled:bg-[var(--muted)] disabled:opacity-70",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
