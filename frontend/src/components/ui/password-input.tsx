"use client";
import * as React from "react";
import { Eye, EyeOff } from "lucide-react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export const PasswordInput = React.forwardRef<
  HTMLInputElement,
  Omit<React.InputHTMLAttributes<HTMLInputElement>, "type">
>(({ className, ...props }, ref) => {
  const [visible, setVisible] = React.useState(false);
  const localRef = React.useRef<HTMLInputElement>(null);
  React.useImperativeHandle(ref, () => localRef.current as HTMLInputElement);
  return (
    <div className="relative">
      <Input type={visible ? "text" : "password"} ref={localRef} className={cn("pr-12", className)} {...props} />
      <button
        type="button"
        onPointerDown={(event) => event.preventDefault()}
        onClick={() => {
          setVisible((current) => !current);
          requestAnimationFrame(() => localRef.current?.focus());
        }}
        aria-label={visible ? "Скрыть пароль" : "Показать пароль"}
        aria-pressed={visible}
        className="absolute inset-y-0 right-0 z-10 flex w-12 touch-manipulation items-center justify-center rounded-r-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ring)]"
      >
        {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
});
PasswordInput.displayName = "PasswordInput";
