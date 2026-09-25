import * as React from "react";
import { cn, PHONE_INPUT_TEXT } from "@/lib/utils";

/**
 * Многострочное поле. mono — текст отчёта или список идентификаторов:
 * моноширинный, 16px на телефоне (iOS не зумит страницу при фокусе).
 */
export function Textarea({ mono = false, className, ...props }: React.ComponentProps<"textarea"> & { mono?: boolean }) {
  return (
    <textarea
      className={cn(
        "w-full resize-y rounded-xl border bg-[var(--background)] px-3 py-2 outline-none transition focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/15",
        mono ? cn("font-mono leading-snug", PHONE_INPUT_TEXT) : "text-sm",
        className,
      )}
      {...props}
    />
  );
}
