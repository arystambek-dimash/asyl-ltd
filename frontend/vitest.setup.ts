import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node 25 exposes an incomplete experimental `localStorage` object when it is
// started without `--localstorage-file`. That non-configured object can shadow
// JSDOM's Storage in test workers. Keep the browser contract deterministic
// across the supported Node 22 runtime and newer developer machines.
function createMemoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() {
      return values.size;
    },
    clear: () => values.clear(),
    getItem: (key) => values.get(String(key)) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => {
      values.delete(String(key));
    },
    setItem: (key, value) => {
      values.set(String(key), String(value));
    },
  };
}

function createBrowserStorage(key: "localStorage" | "sessionStorage"): Storage {
  const frame = document.createElement("iframe");
  document.documentElement.appendChild(frame);
  const storage = frame.contentWindow?.[key];
  frame.remove();
  return storage && typeof storage.getItem === "function" ? storage : createMemoryStorage();
}

for (const key of ["localStorage", "sessionStorage"] as const) {
  if (typeof globalThis[key]?.getItem !== "function") {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      value: createBrowserStorage(key),
    });
  }
}

afterEach(() => {
  cleanup();
});
