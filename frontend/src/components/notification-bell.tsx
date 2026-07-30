"use client";
import { useState, useRef, useEffect } from "react";
import { Bell } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";
import type { Notification } from "@/lib/types";

export function NotificationBell() {
  const { data: items, reload } = useApi<Notification[]>("/portal/notifications/");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const list = items ?? [];
  const unread = list.filter((n) => !n.is_read).length;

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  function closeAndRestoreFocus() {
    setOpen(false);
    requestAnimationFrame(() => triggerRef.current?.focus());
  }

  async function markRead(id: number) {
    try {
      await api.post(`/portal/notifications/${id}/read/`);
      reload();
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative flex size-11 items-center justify-center rounded-xl border bg-[var(--card)] text-[var(--muted-foreground)] shadow-sm transition hover:bg-[var(--secondary)] hover:text-[var(--foreground)]"
        aria-label="Уведомления"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls="portal-notifications"
      >
        <Bell className="size-5" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex min-w-4 items-center justify-center rounded-full bg-[var(--destructive)] px-1 text-[10px] font-semibold leading-4 text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          ref={panelRef}
          id="portal-notifications"
          role="dialog"
          aria-label="Уведомления"
          tabIndex={-1}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              closeAndRestoreFocus();
            }
          }}
          className="fixed left-3 right-3 top-[76px] z-50 max-h-[calc(100dvh-5.5rem)] overflow-hidden rounded-2xl border border-[var(--border)] bg-[var(--card)] shadow-float outline-none sm:absolute sm:left-auto sm:right-0 sm:top-12 sm:w-80"
        >
          <div className="border-b px-4 py-3 text-sm font-semibold">Уведомления</div>
          <div className="max-h-[calc(100dvh-9rem)] overflow-y-auto sm:max-h-96">
            {list.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">Нет уведомлений.</p>
            ) : (
              list.map((n) => (
                <button
                  key={n.id}
                  onClick={() => !n.is_read && markRead(n.id)}
                  className={cn(
                    "flex min-h-14 w-full flex-col gap-1 border-b px-4 py-3 text-left transition-colors last:border-0",
                    n.is_read ? "opacity-60" : "bg-[var(--muted)]/30 hover:bg-[var(--muted)]/50",
                  )}
                >
                  <span className="text-sm">{n.text}</span>
                  <span className="text-[11px] text-[var(--muted-foreground)]">
                    {new Date(n.created_at).toLocaleString("ru-RU", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                    {!n.is_read && " · отметить прочитанным"}
                  </span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
