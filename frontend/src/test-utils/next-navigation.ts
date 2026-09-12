import { useSyncExternalStore } from "react";

/**
 * Стейтфул-мок `next/navigation` для vitest: push/replace/back меняют URL,
 * `usePathname`/`useSearchParams` перерисовывают подписчиков. Подключать так:
 * `vi.mock("next/navigation", () => import("@/test-utils/next-navigation"))`.
 */
const state = { url: "/", stack: ["/"], listeners: new Set<() => void>() };

export const routerCalls = { push: [] as string[], replace: [] as string[], back: 0 };

function notify() {
  state.listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  state.listeners.add(listener);
  return () => state.listeners.delete(listener);
}

export function resetNavigation(url = "/") {
  state.url = url;
  state.stack = [url];
  routerCalls.push = [];
  routerCalls.replace = [];
  routerCalls.back = 0;
  notify();
}

export function currentUrl() {
  return state.url;
}

export function useRouter() {
  return {
    push: (url: string) => {
      routerCalls.push.push(url);
      state.stack.push(url);
      state.url = url;
      notify();
    },
    replace: (url: string) => {
      routerCalls.replace.push(url);
      state.stack[state.stack.length - 1] = url;
      state.url = url;
      notify();
    },
    back: () => {
      routerCalls.back += 1;
      if (state.stack.length > 1) state.stack.pop();
      state.url = state.stack[state.stack.length - 1];
      notify();
    },
    prefetch: () => {},
    refresh: () => {},
  };
}

const pathnameOf = () => state.url.split("?")[0];
const searchOf = () => state.url.split("?")[1] ?? "";

export function usePathname() {
  return useSyncExternalStore(subscribe, pathnameOf, pathnameOf);
}

export function useSearchParams() {
  const search = useSyncExternalStore(subscribe, searchOf, searchOf);
  return new URLSearchParams(search);
}
