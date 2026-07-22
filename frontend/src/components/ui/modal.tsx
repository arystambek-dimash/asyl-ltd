"use client";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

const FOCUSABLE_SELECTOR = [
  "button:not(:disabled)",
  "a[href]",
  "input:not(:disabled)",
  "select:not(:disabled)",
  "textarea:not(:disabled)",
  '[tabindex]:not([tabindex="-1"])',
  '[contenteditable="true"]',
].join(",");

type OpenModal = {
  dialog: HTMLElement | null;
  id: string;
  restoreFocusTo: HTMLElement | null;
};

const openModals: OpenModal[] = [];
let rootRestoreTarget: HTMLElement | null = null;
let scrollLockCount = 0;
let bodyOverflowBeforeLock = "";

function registerModal(entry: OpenModal) {
  if (openModals.length === 0) rootRestoreTarget = entry.restoreFocusTo;
  openModals.push(entry);
}

function unregisterModal(id: string) {
  const index = openModals.findIndex((modal) => modal.id === id);
  if (index === -1) return;
  const entry = openModals[index];
  const wasTopmost = index === openModals.length - 1;
  openModals.splice(index, 1);

  if (!wasTopmost) return;
  const restoreTarget = openModals.length === 0 ? rootRestoreTarget : entry.restoreFocusTo;
  if (openModals.length === 0) rootRestoreTarget = null;
  const canRestore =
    restoreTarget?.isConnected && !restoreTarget.matches(":disabled") && !restoreTarget.closest("[inert]");
  if (canRestore) restoreTarget.focus();

  // The opener can disappear or become disabled while a nested action runs.
  // Keep keyboard focus inside the remaining topmost dialog in that case.
  const remainingDialog = openModals.at(-1)?.dialog;
  if (remainingDialog && !remainingDialog.contains(document.activeElement)) {
    (focusableElements(remainingDialog)[0] ?? remainingDialog).focus();
  }
}

function isTopmostModal(id: string) {
  return openModals.at(-1)?.id === id;
}

function lockBodyScroll() {
  if (scrollLockCount === 0) {
    bodyOverflowBeforeLock = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  scrollLockCount += 1;
}

function unlockBodyScroll() {
  scrollLockCount = Math.max(0, scrollLockCount - 1);
  if (scrollLockCount !== 0) return;
  document.body.style.overflow = bodyOverflowBeforeLock;
  bodyOverflowBeforeLock = "";
}

function focusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => element.tabIndex >= 0 && !element.hidden && !element.closest("[inert]"),
  );
}

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
  const descriptionId = useId();
  const modalId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open || !mounted) return;
    const restoreFocusTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    registerModal({ dialog: dialogRef.current, id: modalId, restoreFocusTo });
    lockBodyScroll();

    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || !isTopmostModal(modalId)) return;
      e.preventDefault();
      onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);

    const dialog = dialogRef.current;
    const initialFocus =
      dialog?.querySelector<HTMLElement>("[data-autofocus], [autofocus]") ??
      (dialog ? focusableElements(dialog)[0] : null) ??
      dialog;
    initialFocus?.focus();

    return () => {
      document.removeEventListener("keydown", onKey);
      unregisterModal(modalId);
      unlockBodyScroll();
    };
  }, [modalId, mounted, open]);

  function trapFocus(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "Tab" || !isTopmostModal(modalId) || !dialogRef.current) return;
    const focusable = focusableElements(dialogRef.current);
    if (focusable.length === 0) {
      e.preventDefault();
      dialogRef.current.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !dialogRef.current.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !dialogRef.current.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  }

  if (!open || !mounted) return null;

  return createPortal(
    <div
      className={cn("fixed inset-0 z-[100] flex items-center justify-center p-4", mobileFullscreen && "max-sm:p-0")}
      onKeyDown={trapFocus}
    >
      <div
        className="absolute inset-0 bg-black/55 backdrop-blur-[1px] animate-modal-backdrop"
        onClick={() => onCloseRef.current()}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "relative z-10 flex max-h-[calc(100dvh-2rem)] w-full max-w-lg flex-col overflow-hidden rounded-xl border bg-[var(--card)] shadow-2xl animate-modal-content",
          mobileFullscreen && "max-sm:h-[100dvh] max-sm:max-h-[100dvh] max-sm:rounded-none max-sm:border-0",
          className,
        )}
      >
        <div className="relative border-b px-4 pb-4 pt-5 sm:px-6 sm:pt-6">
          {eyebrow && <div className="text-[12px] text-[var(--muted-foreground)]">{eyebrow}</div>}
          <h2 id={titleId} className="text-[22px] font-bold tracking-tight">
            {title}
          </h2>
          {description && (
            <p id={descriptionId} className="mt-1 text-[14px] text-[var(--muted-foreground)]">
              {description}
            </p>
          )}
          <button
            type="button"
            onClick={() => onCloseRef.current()}
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
    document.body,
  );
}
