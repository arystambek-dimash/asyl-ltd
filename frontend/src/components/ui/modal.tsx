"use client";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function Modal({
  open,
  onClose,
  title,
  eyebrow,
  description,
  footer,
  children,
  className,
  mobileFullscreen = false,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  eyebrow?: string;
  description?: string;
  footer?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  mobileFullscreen?: boolean;
}) {
  const [mounted, setMounted] = useState(false);
  const titleId = useId();

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const previousOverflow = document.body.style.overflow;
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, onClose]);

  if (!open || !mounted) return null;

  return createPortal(
    <div
      className={cn(
        "fixed inset-0 z-[100] flex items-center justify-center p-4",
        mobileFullscreen && "max-sm:p-0",
      )}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div
        className="absolute inset-0 bg-black/55 backdrop-blur-[1px] animate-modal-backdrop"
        onClick={onClose}
      />
      <div
        className={cn(
          "relative z-10 flex max-h-[calc(100dvh-2rem)] w-full max-w-lg flex-col overflow-hidden rounded-xl border bg-[var(--card)] shadow-2xl animate-modal-content",
          mobileFullscreen && "max-sm:h-[100dvh] max-sm:max-h-[100dvh] max-sm:rounded-none max-sm:border-0",
          className
        )}
      >
        <div className="relative border-b px-4 pb-4 pt-5 sm:px-6 sm:pt-6">
          {eyebrow && (
            <div className="text-[12px] text-[var(--muted-foreground)]">{eyebrow}</div>
          )}
          <h2 id={titleId} className="text-[22px] font-bold tracking-tight">{title}</h2>
          {description && (
            <p className="mt-1 text-[14px] text-[var(--muted-foreground)]">{description}</p>
          )}
          <button
            type="button"
            onClick={onClose}
            className="absolute right-4 top-4 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--accent)] hover:text-[var(--foreground)] focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-[var(--ring)]/50"
            aria-label="Закрыть"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="overflow-y-auto p-4 sm:p-6">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 border-t bg-[var(--muted)]/40 px-4 py-3 sm:px-6">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body
  );
}
