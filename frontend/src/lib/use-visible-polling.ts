"use client";

import { useEffect, useRef } from "react";

/**
 * Run one visible-page poll at a time and wait for it to settle before
 * scheduling the next tick. Reconnect/visibility events request an immediate
 * tick without starting a second overlapping request.
 */
export function useVisiblePolling(poll: () => Promise<unknown>, intervalMs: number, active = true) {
  const pollRef = useRef(poll);
  pollRef.current = poll;

  useEffect(() => {
    if (!active) return;
    let disposed = false;
    let running = false;
    let rerun = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = () => {
      if (disposed) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void run();
      }, intervalMs);
    };

    const run = async () => {
      if (disposed) return;
      if (document.hidden) {
        schedule();
        return;
      }
      if (running) {
        rerun = true;
        return;
      }
      running = true;
      try {
        await pollRef.current();
      } catch {
        // Poll owners expose their own error state. A transient rejection must
        // not stop future ticks or surface as an unhandled promise rejection.
      } finally {
        running = false;
        if (disposed) return;
        if (rerun) {
          rerun = false;
          void run();
        } else {
          schedule();
        }
      }
    };

    const runNow = () => {
      if (document.hidden || disposed) return;
      if (timer) clearTimeout(timer);
      timer = null;
      void run();
    };

    schedule();
    document.addEventListener("visibilitychange", runNow);
    window.addEventListener("online", runNow);
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", runNow);
      window.removeEventListener("online", runNow);
    };
  }, [active, intervalMs]);
}
