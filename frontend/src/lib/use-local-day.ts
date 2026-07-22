"use client";

import { useEffect, useState } from "react";
import { todayLocalIsoDate } from "@/lib/utils";

function millisecondsUntilTomorrow(now: Date): number {
  const tomorrow = new Date(now);
  tomorrow.setHours(24, 0, 0, 0);
  return Math.max(1_000, tomorrow.getTime() - now.getTime() + 50);
}

/** A local calendar-day key that stays correct on long-lived dashboard tabs. */
export function useLocalDay(): string {
  const [day, setDay] = useState(todayLocalIsoDate);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;

    const schedule = () => {
      if (timer) clearTimeout(timer);
      const now = new Date();
      setDay(todayLocalIsoDate());
      timer = setTimeout(schedule, millisecondsUntilTomorrow(now));
    };
    const refreshVisible = () => {
      if (document.visibilityState === "visible") schedule();
    };

    schedule();
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, []);

  return day;
}
