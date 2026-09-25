import { useSyncExternalStore } from "react";

/**
 * Стейтфул-мок `next/navigation` для vitest: push/replace/back меняют URL,
 * `usePathname`/`useSearchParams` перерисовывают подписчиков. Подключать так:
 * `vi.mock("next/navigation", () => import("@/test-utils/next-navigation"))`.
 */
const state = { url: "/", stack: ["/"], listeners: new Set<() => void>() };

type NavigateOptions = { scroll?: boolean };

/** Адреса push/replace по порядку; `options` — их вторые аргументы в том же порядке. */
export const routerCalls = {
  push: [] as string[],
  replace: [] as string[],
  options: [] as (NavigateOptions | undefined)[],
  back: 0,
};

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
  routerCalls.options = [];
  routerCalls.back = 0;
  notify();
}

export function currentUrl() {
  return state.url;
}

export function useRouter() {
  return {
    push: (url: string, options?: NavigateOptions) => {
      routerCalls.push.push(url);
      routerCalls.options.push(options);
      state.stack.push(url);
      state.url = url;
      notify();
    },
    replace: (url: string, options?: NavigateOptions) => {
      routerCalls.replace.push(url);
      routerCalls.options.push(options);
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
