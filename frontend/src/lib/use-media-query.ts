"use client";
import { useCallback, useSyncExternalStore } from "react";

/** Ширина, с которой касса переключается на мобильную раскладку (граница `md`). */
export const MOBILE_MEDIA_QUERY = "(max-width: 767px)";

function mediaList(query: string): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia(query);
}

/** Реактивный `matchMedia`. На сервере и в jsdom без matchMedia — всегда false. */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const media = mediaList(query);
      if (!media) return () => {};
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => mediaList(query)?.matches ?? false,
    () => false,
  );
}

export function useIsMobile(): boolean {
  return useMediaQuery(MOBILE_MEDIA_QUERY);
}
