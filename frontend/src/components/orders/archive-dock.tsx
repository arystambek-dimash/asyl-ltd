"use client";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Archive, ChevronDown, RotateCcw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api, apiError } from "@/lib/api";
import { cn, currencySymbol, formatDateTime, formatMoney } from "@/lib/utils";
import { useDismiss } from "@/lib/use-dismiss";
import type { Order } from "@/lib/types";

/** Floating archive preview. It behaves as a non-modal dialog: focus enters
 * the preview when opened, may leave with Tab, and returns to the trigger when
 * dismissed with Escape. */
export function ArchiveDock({
  trashed,
  count,
  onOpenArchive,
  onChanged,
}: {
  trashed: Order[];
  count: number;
  onOpenArchive: () => void;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [purgeItem, setPurgeItem] = useState<Order | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  // Пока открыт диалог удаления, клик по нему не должен схлопывать стопку.
  useDismiss(rootRef, () => setOpen(false), open && !purgeItem);

  useEffect(() => {
    if (!open) return;
    const focusFrame = requestAnimationFrame(() => {
      const firstAction = panelRef.current?.querySelector<HTMLElement>(
        'button:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])',
      );
      (firstAction ?? panelRef.current)?.focus();
    });
    return () => cancelAnimationFrame(focusFrame);
  }, [open]);

  useEffect(() => {
    if (!open || purgeItem) return;
    const restoreOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      requestAnimationFrame(() => triggerRef.current?.focus());
    };
    document.addEventListener("keydown", restoreOnEscape, true);
    return () => document.removeEventListener("keydown", restoreOnEscape, true);
  }, [open, purgeItem]);

  // Веер стопки: ближе к кнопке — удалённые последними.
  const recent = [...trashed].sort((a, b) => (b.deleted_at ?? "").localeCompare(a.deleted_at ?? "")).slice(0, 4);
  const fan = [...recent].reverse();

  async function act(order: Order, action: () => Promise<unknown>) {
    setBusyId(order.id);
    setError("");
    try {
      await action();
      onChanged();
    } catch (cause) {
      setError(apiError(cause));
      throw cause;
    } finally {
      setBusyId(null);
    }
  }

  const restore = (order: Order) => act(order, () => api.post(`/orders/${order.id}/restore/`)).catch(() => {});
  const purge = (order: Order) =>
    act(order, () => api.delete(`/orders/${order.id}/purge/`))
      .then(() => setPurgeItem(null))
      .catch(() => setPurgeItem(null));

  // Задержки анимации: карточки «выезжают» из кнопки снизу вверх.
  const delay = (indexFromBottom: number) => ({ animationDelay: `${indexFromBottom * 45}ms` });

  // Портал в body: у контента AppShell есть transform (animate-fade-up),
  // внутри него fixed считается от контейнера, а не от окна.
  return createPortal(
    <div
      ref={rootRef}
      onBlur={(event) => {
        if (open && !purgeItem && !event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
      }}
      className="fixed bottom-5 right-4 z-[90] flex flex-col items-end sm:bottom-6 sm:right-6"
    >
      {open && (
        <div
          ref={panelRef}
          id={panelId}
          role="dialog"
          aria-label="Последние заказы в архиве"
          tabIndex={-1}
          className="mb-3 flex w-[300px] max-w-[calc(100vw-2rem)] flex-col gap-2"
        >
          <button
            type="button"
            style={delay(fan.length + 1)}
            onClick={() => {
              triggerRef.current?.focus();
              setOpen(false);
              onOpenArchive();
            }}
            className="animate-fade-up flex items-center justify-center gap-1.5 self-center rounded-full border bg-[var(--popover)] px-4 py-1.5 text-xs font-medium shadow-lg transition-colors hover:bg-[var(--accent)]"
          >
            Открыть архив{count > 0 ? ` (${count})` : ""}
            <ChevronDown className="size-3.5 -rotate-90" />
          </button>
          {error && (
            <p
              role="alert"
              style={delay(fan.length)}
              className="animate-fade-up rounded-lg border bg-[var(--popover)] px-3 py-2 text-xs text-[var(--destructive)] shadow-lg"
            >
              {error}
            </p>
          )}
          {fan.length === 0 ? (
            <div
              style={delay(0)}
              className="animate-fade-up rounded-xl border bg-[var(--popover)] px-4 py-5 text-center text-sm text-[var(--muted-foreground)] shadow-lg"
            >
              Архив пуст.
            </div>
          ) : (
            fan.map((order, index) => (
              <div
                key={order.id}
                style={delay(fan.length - 1 - index)}
                className="animate-fade-up flex items-center justify-between gap-2 rounded-xl border bg-[var(--popover)] p-3 shadow-[0_10px_35px_rgba(0,0,0,0.16)]"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="font-semibold">#{order.id}</span>
                    <span className="truncate">{order.client_name || `Клиент #${order.client}`}</span>
                  </div>
                  <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                    <span className="tabular-nums">
                      {formatMoney(order.total_amount)} {currencySymbol(order.currency)}
                    </span>
                    {order.deleted_at && <> · {formatDateTime(order.deleted_at)}</>}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busyId === order.id}
                    title="Восстановить заказ"
                    onClick={() => restore(order)}
                  >
                    <RotateCcw className="size-3.5" /> Вернуть
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={busyId === order.id}
                    className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                    title="Удалить навсегда"
                    onClick={() => setPurgeItem(order)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      <ConfirmDialog
        open={!!purgeItem}
        onClose={() => setPurgeItem(null)}
        title="Удалить заказ навсегда?"
        description={
          purgeItem
            ? `Заказ #${purgeItem.id} (${purgeItem.client_name ?? "клиент"}) будет удалён безвозвратно вместе с позициями и оплатами. Восстановить его будет нельзя.`
            : ""
        }
        confirmLabel="Удалить навсегда"
        busy={purgeItem ? busyId === purgeItem.id : false}
        error={error}
        onConfirm={() => purgeItem && purge(purgeItem)}
      />

      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((current) => !current)}
        title="Архив заказов"
        aria-label="Архив заказов"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        className={cn(
          "relative flex size-12 items-center justify-center rounded-2xl border shadow-lg transition-all",
          open
            ? "border-[var(--foreground)] bg-[var(--foreground)] text-[var(--background)]"
            : "bg-[var(--card)] text-[var(--muted-foreground)] hover:-translate-y-0.5 hover:text-[var(--foreground)] hover:shadow-xl",
        )}
      >
        <Archive className="size-5" />
        {count > 0 && !open && (
          <span className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--foreground)] px-1.5 text-[11px] font-semibold tabular-nums text-[var(--background)]">
            {count}
          </span>
        )}
      </button>
    </div>,
    document.body,
  );
}
