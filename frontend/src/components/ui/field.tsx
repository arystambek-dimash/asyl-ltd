import * as React from "react";
import { cn } from "@/lib/utils";
import { Label } from "./label";

/** id текста ошибки поля — для aria-describedby у самого контрола. */
export const fieldErrorId = (htmlFor: string) => `${htmlFor}-error`;

export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
  className,
}: {
  label?: React.ReactNode;
  htmlFor?: string;
  hint?: React.ReactNode;
  /** Ошибка заменяет подсказку, чтобы под полем не было двух строк. */
  error?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col", className)}>
      {label && <Label htmlFor={htmlFor}>{label}</Label>}
      {children}
      {error ? (
        <p id={htmlFor && fieldErrorId(htmlFor)} className="mt-1.5 text-[12px] text-[var(--destructive)]">
          {error}
        </p>
      ) : (
        hint && <p className="mt-1.5 text-[12px] text-[var(--muted-foreground)]">{hint}</p>
      )}
    </div>
  );
}
