import * as React from "react";
import { cn } from "@/lib/utils";

export function Table({
  className,
  "aria-label": ariaLabel = "Таблица данных",
  ...props
}: React.HTMLAttributes<HTMLTableElement>) {
  return (
    <div
      className="w-full overflow-x-auto rounded-[inherit] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ring)]"
      role="region"
      aria-label={ariaLabel}
      tabIndex={0}
    >
      <table
        aria-label={ariaLabel}
        className={cn("w-full border-separate border-spacing-0 text-[13px] sm:text-[14px]", className)}
        {...props}
      />
    </div>
  );
}
export function THead({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead
      className={cn(
        "bg-[var(--muted)]/65 text-[10px] font-bold uppercase tracking-[0.08em] text-[var(--muted-foreground)]",
        className,
      )}
      {...props}
    />
  );
}
export function TBody({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={cn("[&>tr:last-child>td]:border-0", className)} {...props} />;
}
export function TR({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn(
        "group transition-colors hover:bg-[var(--accent)]/55 [&>td]:border-b [&>td]:border-[var(--border)]",
        className,
      )}
      {...props}
    />
  );
}
export function TH({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      className={cn("h-11 px-3 text-left align-middle font-bold text-[var(--muted-foreground)] sm:px-4", className)}
      {...props}
    />
  );
}
export function TD({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("h-14 px-3 align-middle sm:px-4", className)} {...props} />;
}
