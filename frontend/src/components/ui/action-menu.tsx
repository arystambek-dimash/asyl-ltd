"use client";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MoreVertical } from "lucide-react";
import { cn } from "@/lib/utils";
import { useDismiss } from "@/lib/use-dismiss";

export interface ActionMenuItem {
  key: string;
  label: string;
  icon?: React.ElementType;
  tone?: "destructive";
  /** Пункт виден, но неактивен; hint объясняет почему. */
  disabled?: boolean;
  hint?: string;
  onSelect: () => void;
}

/** Кебаб-меню действий строки («⋮»). Меню рендерится порталом с фиксированной
 * позицией — не обрезается overflow-обёрткой таблицы. */
export function ActionMenu({
  items,
  label = "Действия",
  className,
}: {
  items: ActionMenuItem[];
  label?: string;
  className?: string;
}) {
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const triggerId = `${menuId}-trigger`;
  const focusEdgeRef = useRef<"first" | "last">("first");
  const open = pos !== null;
  useDismiss(menuRef, () => setPos(null), open, [triggerRef]);

  useEffect(() => {
    if (!open) return;
    const enabledItems = menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)');
    if (!enabledItems?.length) {
      menuRef.current?.focus();
      return;
    }
    const index = focusEdgeRef.current === "last" ? enabledItems.length - 1 : 0;
    enabledItems[index]?.focus();
  }, [open]);

  function openMenu(button: HTMLButtonElement, focusEdge: "first" | "last" = "first") {
    const rect = button.getBoundingClientRect();
    focusEdgeRef.current = focusEdge;
    setPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
  }

  function toggle(e: React.MouseEvent<HTMLButtonElement>) {
    // Меню живёт в кликабельных строках/карточках — клик не должен открывать заказ.
    e.stopPropagation();
    if (open) {
      setPos(null);
      return;
    }
    openMenu(e.currentTarget);
  }

  function onTriggerKeyDown(e: React.KeyboardEvent<HTMLButtonElement>) {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    e.stopPropagation();
    if (!open) openMenu(e.currentTarget, e.key === "ArrowUp" ? "last" : "first");
  }

  function onMenuKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const enabledItems = Array.from(
      menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [],
    );
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setPos(null);
      triggerRef.current?.focus();
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      setPos(null);
      triggerRef.current?.focus();
      return;
    }
    if (!enabledItems.length) return;

    const currentIndex = enabledItems.indexOf(document.activeElement as HTMLButtonElement);
    let nextIndex: number | null = null;
    if (e.key === "ArrowDown") nextIndex = (currentIndex + 1) % enabledItems.length;
    if (e.key === "ArrowUp") nextIndex = (currentIndex - 1 + enabledItems.length) % enabledItems.length;
    if (e.key === "Home") nextIndex = 0;
    if (e.key === "End") nextIndex = enabledItems.length - 1;
    if (nextIndex === null) return;
    e.preventDefault();
    e.stopPropagation();
    enabledItems[nextIndex]?.focus();
  }

  return (
    <>
      <button
        ref={triggerRef}
        id={triggerId}
        type="button"
        title={label}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={toggle}
        onKeyDown={onTriggerKeyDown}
        className={cn(
          "inline-flex size-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors",
          "hover:bg-[var(--accent)] hover:text-[var(--foreground)]",
          open && "bg-[var(--accent)] text-[var(--foreground)]",
          className,
        )}
      >
        <MoreVertical className="size-4" />
      </button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            id={menuId}
            role="menu"
            tabIndex={-1}
            aria-labelledby={triggerId}
            style={{ top: pos.top, right: pos.right }}
            onClick={(event) => event.stopPropagation()}
            onKeyDown={onMenuKeyDown}
            className="fixed z-[120] w-48 rounded-xl border bg-[var(--popover)] p-1 shadow-[0_12px_40px_rgba(0,0,0,0.18)] animate-modal-content"
          >
            {items.map((item) => (
              <button
                key={item.key}
                type="button"
                role="menuitem"
                disabled={item.disabled}
                onClick={() => {
                  if (item.disabled) return;
                  // Preserve a stable return target before the portalled item is
                  // removed and a selected action potentially opens a Modal.
                  triggerRef.current?.focus();
                  setPos(null);
                  item.onSelect();
                }}
                className={cn(
                  "flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                  item.disabled
                    ? "cursor-default text-[var(--muted-foreground)] opacity-60"
                    : item.tone === "destructive"
                      ? "text-[var(--destructive)] hover:bg-[var(--accent)] hover:text-[var(--destructive)]"
                      : "text-[var(--foreground)] hover:bg-[var(--accent)]",
                )}
              >
                {item.icon && <item.icon className="mt-0.5 size-4 shrink-0" />}
                <span className="min-w-0">
                  {item.label}
                  {item.disabled && item.hint && (
                    <span className="mt-0.5 block text-xs text-[var(--muted-foreground)]">{item.hint}</span>
                  )}
                </span>
              </button>
            ))}
          </div>,
          document.body,
        )}
    </>
  );
}
