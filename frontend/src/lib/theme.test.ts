import { afterEach, expect, it, vi } from "vitest";
import { THEME_INIT_SCRIPT, THEME_KEY, pickTheme, readTheme } from "./theme";

function mockSystemTheme(dark: boolean) {
  const listeners = new Set<() => void>();
  const media = {
    matches: dark,
    addEventListener: (_: string, listener: () => void) => listeners.add(listener),
    removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => media),
  );
  return {
    change(next: boolean) {
      media.matches = next;
      listeners.forEach((listener) => listener());
    },
  };
}

function runInitScript() {
  new Function(THEME_INIT_SCRIPT)();
}

afterEach(() => {
  localStorage.removeItem(THEME_KEY);
  document.documentElement.classList.remove("dark");
  vi.unstubAllGlobals();
});

it("applies the saved dark theme before React mounts anything", () => {
  mockSystemTheme(false);
  localStorage.setItem(THEME_KEY, "dark");
  runInitScript();
  expect(document.documentElement).toHaveClass("dark");
});

it("stays light without a saved choice even when the system is dark", () => {
  mockSystemTheme(true);
  runInitScript();
  expect(document.documentElement).not.toHaveClass("dark");
});

it("follows the system theme for «Авто» on every page, including live changes", () => {
  const system = mockSystemTheme(true);
  localStorage.setItem(THEME_KEY, "system");
  runInitScript();
  expect(document.documentElement).toHaveClass("dark");

  system.change(false);
  expect(document.documentElement).not.toHaveClass("dark");
});

it("stores and applies the picked theme; unknown values fall back to light", () => {
  mockSystemTheme(false);
  localStorage.setItem(THEME_KEY, "sepia");
  expect(readTheme()).toBe("light");

  pickTheme("dark");
  expect(readTheme()).toBe("dark");
  expect(document.documentElement).toHaveClass("dark");

  pickTheme("light");
  expect(document.documentElement).not.toHaveClass("dark");
});
