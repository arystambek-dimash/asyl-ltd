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

  const list = items ?? [];
  const unread = list.filter((n) => !n.is_read).length;

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  async function markRead(id: number) {
    try { await api.post(`/portal/notifications/${id}/read/`); reload(); } catch { /* ignore */ }
  }

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen((v) => !v)}
        className="relative text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        aria-label="Уведомления">
        <Bell className="size-5" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex min-w-4 items-center justify-center rounded-full bg-[var(--destructive)] px-1 text-[10px] font-semibold leading-4 text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-9 z-50 w-80 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-lg">
          <div className="border-b px-4 py-3 text-sm font-semibold">Уведомления</div>
          <div className="max-h-96 overflow-y-auto">
            {list.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">Нет уведомлений.</p>
            ) : (
              list.map((n) => (
                <button key={n.id} onClick={() => !n.is_read && markRead(n.id)}
                  className={cn("flex w-full flex-col gap-1 border-b px-4 py-3 text-left last:border-0 transition-colors",
                    n.is_read ? "opacity-60" : "bg-[var(--muted)]/30 hover:bg-[var(--muted)]/50")}>
                  <span className="text-sm">{n.text}</span>
                  <span className="text-[11px] text-[var(--muted-foreground)]">
                    {new Date(n.created_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}
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
