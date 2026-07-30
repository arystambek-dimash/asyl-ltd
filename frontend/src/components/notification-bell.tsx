"use client";
import { useId, useRef, useState } from "react";
import { Bell } from "lucide-react";
import { api, apiError } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { useDismiss } from "@/lib/use-dismiss";
import { showToast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import type { Notification } from "@/lib/types";

const NOTIFICATION_DATE_FORMATTER = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

export function NotificationBell() {
  const { data: items, loading, error, reload } = useApi<Notification[]>("/portal/notifications/");
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  const list = items ?? [];
  const unread = list.filter((n) => !n.is_read).length;

  useDismiss(ref, () => setOpen(false), open);

  async function markRead(id: number) {
    try {
      await api.post(`/portal/notifications/${id}/read/`);
      await reload();
    } catch (cause) {
      const message = apiError(cause);
      if (message) showToast(message);
    }
  }

  return (
    <div
      className="relative"
      ref={ref}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || !open) return;
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="relative text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        aria-label={unread > 0 ? `Уведомления: ${unread} непрочитанных` : "Уведомления"}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
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
          id={panelId}
          role="dialog"
          aria-label="Уведомления"
          className="absolute right-0 top-9 z-50 w-80 overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--card)] shadow-lg"
        >
          <div className="border-b px-4 py-3 text-sm font-semibold">Уведомления</div>
          <div className="max-h-96 overflow-y-auto">
            {loading && !items ? (
              <p className="px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
            ) : error && !items ? (
              <div className="grid gap-2 px-4 py-5 text-center text-sm">
                <p role="alert" className="text-[var(--destructive)]">
                  {error}
                </p>
                <button
                  type="button"
                  onClick={() => void reload()}
                  className="font-medium text-[var(--primary)] hover:underline"
                >
                  Повторить
                </button>
              </div>
            ) : list.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-[var(--muted-foreground)]">Нет уведомлений.</p>
            ) : (
              list.map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => !n.is_read && markRead(n.id)}
                  className={cn(
                    "flex w-full flex-col gap-1 border-b px-4 py-3 text-left last:border-0 transition-colors",
                    n.is_read ? "opacity-60" : "bg-[var(--muted)]/30 hover:bg-[var(--muted)]/50",
                  )}
                >
                  <span className="text-sm">{n.text}</span>
                  <span className="text-[11px] text-[var(--muted-foreground)]">
                    {NOTIFICATION_DATE_FORMATTER.format(new Date(n.created_at))}
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
