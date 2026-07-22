import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLocalDay } from "@/lib/use-local-day";

describe("useLocalDay", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("rolls the local day key over after midnight", async () => {
    vi.setSystemTime(new Date(2026, 6, 22, 23, 59, 59, 500));
    const { result } = renderHook(() => useLocalDay());

    expect(result.current).toBe("2026-07-22");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });

    expect(result.current).toBe("2026-07-23");
  });
});
