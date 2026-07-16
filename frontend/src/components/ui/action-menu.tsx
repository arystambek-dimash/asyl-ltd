"use client";
import { useRef, useState } from "react";
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
export function ActionMenu({ items, label = "Действия", className }: {
  items: ActionMenuItem[];
  label?: string;
  className?: string;
}) {
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const open = pos !== null;
  useDismiss(menuRef, () => setPos(null), open);

  function toggle(e: React.MouseEvent<HTMLButtonElement>) {
    // Меню живёт в кликабельных строках/карточках — клик не должен открывать заказ.
    e.stopPropagation();
    if (open) { setPos(null); return; }
    const r = e.currentTarget.getBoundingClientRect();
    setPos({ top: r.bottom + 4, right: window.innerWidth - r.right });
  }

  return (
    <>
      <button
        type="button"
        title={label}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={toggle}
        className={cn(
          "inline-flex size-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors",
          "hover:bg-[var(--accent)] hover:text-[var(--foreground)]",
          open && "bg-[var(--accent)] text-[var(--foreground)]",
          className,
        )}
      >
        <MoreVertical className="size-4" />
      </button>

      {open && createPortal(
        <div
          ref={menuRef}
          role="menu"
          style={{ top: pos.top, right: pos.right }}
          className="fixed z-[120] w-48 rounded-xl border bg-[var(--popover)] p-1 shadow-[0_12px_40px_rgba(0,0,0,0.18)] animate-modal-content"
        >
          {items.map((item) => (
            <button
              key={item.key}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              onClick={() => { if (item.disabled) return; setPos(null); item.onSelect(); }}
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
