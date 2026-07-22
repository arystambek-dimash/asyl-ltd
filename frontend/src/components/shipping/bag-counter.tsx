"use client";

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { apiError } from "@/lib/api";
import { orderedBagCount } from "@/lib/orders";
import type { Order } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface BagCounterHandle {
  /** Persist the latest visible value and reject if it could not be saved. */
  saveNow: () => Promise<number>;
}

/** Large touch-friendly bag counter with debounced, serialized persistence. */
export const BagCounter = forwardRef<
  BagCounterHandle,
  {
    order: Order;
    onSave: (bags: number) => Promise<unknown>;
  }
>(function BagCounter({ order, onSave }, ref) {
  const [bags, setBags] = useState(order.bags_loaded ?? 0);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastSaved = useRef(order.bags_loaded ?? 0);
  const pending = useRef<number | null>(null);
  const queued = useRef<number | null>(null);
  const inFlight = useRef<Promise<void> | null>(null);
  const mounted = useRef(true);
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  useEffect(() => {
    const remote = order.bags_loaded ?? 0;
    const hasLocalChanges =
      timer.current !== null || pending.current !== null || queued.current !== null || inFlight.current !== null;
    if (remote !== lastSaved.current && !hasLocalChanges) {
      lastSaved.current = remote;
      setBags(remote);
    }
  }, [order.bags_loaded]);

  const flush = useCallback((): Promise<void> => {
    if (inFlight.current) return inFlight.current;
    if (queued.current === null) return Promise.resolve();
    if (mounted.current) {
      setSaving(true);
      setError("");
    }
    let failure: unknown = null;
    const run = async () => {
      while (queued.current !== null) {
        const value = queued.current;
        queued.current = null;
        try {
          await onSaveRef.current(value);
          lastSaved.current = value;
          failure = null;
          if (mounted.current) setError("");
        } catch (cause) {
          failure = cause;
          if (mounted.current) setError(apiError(cause));
          // Keep the unsaved value retryable. If a newer value arrived while
          // this request was active, prefer that value and try it next.
          if (queued.current === null) queued.current = value;
          if (queued.current === value) break;
        }
      }
      if (failure) throw failure;
    };

    const request = run().finally(() => {
      inFlight.current = null;
      if (mounted.current) setSaving(false);
      // An event can enqueue work after the loop's final check but before this
      // microtask. Chain it so callers awaiting saveNow still await everything.
      if (!failure && queued.current !== null) return flush();
    });
    inFlight.current = request;
    return request;
  }, []);

  const enqueueSave = useCallback(
    (value: number) => {
      queued.current = value;
      void flush().catch(() => {});
    },
    [flush],
  );

  const saveNow = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    if (pending.current !== null) {
      queued.current = pending.current;
      pending.current = null;
    }
    return flush().then(() => lastSaved.current);
  }, [flush]);

  useImperativeHandle(ref, () => ({ saveNow }), [saveNow]);

  function change(delta: number) {
    setBags((previous) => {
      const next = Math.max(0, previous + delta);
      pending.current = next;
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        timer.current = null;
        pending.current = null;
        enqueueSave(next);
      }, 700);
      return next;
    });
  }

  useEffect(() => {
    // React Strict Mode replays effects in development, so the mounted flag
    // must be restored on every setup rather than only during hook creation.
    mounted.current = true;
    return () => {
      mounted.current = false;
      void saveNow().catch(() => {});
    };
  }, [saveNow]);

  const ordered = orderedBagCount(order);
  const pct = ordered > 0 ? Math.min(100, Math.round((bags / ordered) * 100)) : 0;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <div className="flex items-center justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-[var(--muted-foreground)]">
            Погружено мешков
          </span>
          <span className={cn("text-xs tabular-nums", saving ? "text-[var(--muted-foreground)]" : "opacity-0")}>
            сохранение…
          </span>
        </div>
        <div className="mt-1 flex items-baseline gap-2">
          <span
            className={cn(
              "text-6xl font-bold tabular-nums leading-none tracking-tight sm:text-7xl",
              pct >= 100 && "text-[var(--success)]",
            )}
          >
            {bags}
          </span>
          <span className="text-xl text-[var(--muted-foreground)]">/ {ordered}</span>
          <span
            className={cn(
              "ml-auto text-lg font-semibold tabular-nums",
              pct >= 100 ? "text-[var(--success)]" : "text-[var(--muted-foreground)]",
            )}
          >
            {pct}%
          </span>
        </div>
      </div>

      <div
        className="h-2.5 overflow-hidden rounded-full bg-[var(--muted)]"
        role="progressbar"
        aria-label="Прогресс погрузки"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
      >
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{
            width: `${pct}%`,
            background: pct >= 100 ? "var(--success)" : "var(--warning)",
          }}
        />
      </div>

      <div className="grid grid-cols-[1fr_1.4fr_1.4fr] gap-2">
        <Button
          type="button"
          variant="outline"
          className="h-16 rounded-xl"
          disabled={bags <= 0}
          onClick={() => change(-1)}
          aria-label="Минус один мешок"
        >
          <Minus className="size-6" />
        </Button>
        <Button
          type="button"
          variant="outline"
          className="h-16 rounded-xl text-xl font-semibold"
          onClick={() => change(1)}
          aria-label="Плюс один мешок"
        >
          <Plus className="size-5" /> 1
        </Button>
        <Button
          type="button"
          variant="outline"
          className="h-16 rounded-xl text-xl font-semibold"
          onClick={() => change(5)}
          aria-label="Плюс пять мешков"
        >
          <Plus className="size-5" /> 5
        </Button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}
    </div>
  );
});
