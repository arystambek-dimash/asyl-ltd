import * as React from "react";
import { cn } from "@/lib/utils";
import { Label } from "./label";

export function Field({
  label,
  htmlFor,
  hint,
  children,
  className,
}: {
  label?: React.ReactNode;
  htmlFor?: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col", className)}>
      {label && <Label htmlFor={htmlFor}>{label}</Label>}
      {children}
      {hint && <p className="mt-1.5 text-[12px] text-[var(--muted-foreground)]">{hint}</p>}
    </div>
  );
}
