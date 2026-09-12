import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useIsMobile } from "./use-media-query";

type Listener = () => void;

function installMatchMedia(matches: boolean) {
  const listeners = new Set<Listener>();
  const media = {
    matches,
    media: "",
    addEventListener: (_event: string, listener: Listener) => listeners.add(listener),
    removeEventListener: (_event: string, listener: Listener) => listeners.delete(listener),
  };
  Object.defineProperty(window, "matchMedia", { configurable: true, writable: true, value: vi.fn(() => media) });
  return {
    resize(next: boolean) {
      media.matches = next;
      listeners.forEach((listener) => listener());
    },
  };
}

afterEach(() => {
  Reflect.deleteProperty(window, "matchMedia");
});

it("is false where matchMedia is unavailable", () => {
  const { result } = renderHook(() => useIsMobile());
  expect(result.current).toBe(false);
});

it("follows the media query", () => {
  const viewport = installMatchMedia(true);
  const { result } = renderHook(() => useIsMobile());
  expect(result.current).toBe(true);
  act(() => viewport.resize(false));
  expect(result.current).toBe(false);
});
