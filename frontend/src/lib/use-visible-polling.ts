"use client";

import { useEffect, useRef } from "react";

interface VisiblePollTick {
  /** Aborted when polling stops or restarts: unmount, `active` off, new `resetKey`. */
  signal: AbortSignal;
  /** The first poll since polling (re)started. */
  first: boolean;
}

interface VisiblePollingOptions {
  /** Run the first poll right away instead of after one interval. */
  immediate?: boolean;
  /** A new value restarts polling: the in-flight poll is aborted, the next one is `first` again. */
  resetKey?: string | number;
}

/**
 * Run one visible-page poll at a time and wait for it to settle before
 * scheduling the next tick. Reconnect/visibility events request an immediate
 * tick without starting a second overlapping request. A function interval is
 * read before every tick, so a poll owner can back off after failures.
 */
export function useVisiblePolling(
  poll: (tick: VisiblePollTick) => Promise<unknown>,
  intervalMs: number | (() => number),
  active = true,
  { immediate = false, resetKey }: VisiblePollingOptions = {},
) {
  const pollRef = useRef(poll);
  pollRef.current = poll;
  const intervalRef = useRef(intervalMs);
  intervalRef.current = intervalMs;
  const fixedInterval = typeof intervalMs === "number" ? intervalMs : null;

  useEffect(() => {
    if (!active) return;
    const controller = new AbortController();
    let first = true;
    let running = false;
    let rerun = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = () => {
      if (controller.signal.aborted) return;
      if (timer) clearTimeout(timer);
      const interval = intervalRef.current;
      timer = setTimeout(
        () => {
          timer = null;
          void run();
        },
        typeof interval === "function" ? interval() : interval,
      );
    };

    const run = async () => {
      if (controller.signal.aborted) return;
      if (document.hidden) {
        schedule();
        return;
      }
      if (running) {
        rerun = true;
        return;
      }
      running = true;
      const tick = { signal: controller.signal, first };
      first = false;
      try {
        await pollRef.current(tick);
      } catch {
        // Poll owners expose their own error state. A transient rejection must
        // not stop future ticks or surface as an unhandled promise rejection.
      } finally {
        running = false;
        if (!controller.signal.aborted) {
          if (rerun) {
            rerun = false;
            void run();
          } else {
            schedule();
          }
        }
      }
    };

    const runNow = () => {
      if (document.hidden || controller.signal.aborted) return;
      if (timer) clearTimeout(timer);
      timer = null;
      void run();
    };

    if (immediate) void run();
    else schedule();
    document.addEventListener("visibilitychange", runNow);
    window.addEventListener("online", runNow);
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", runNow);
      window.removeEventListener("online", runNow);
    };
  }, [active, fixedInterval, immediate, resetKey]);
}
