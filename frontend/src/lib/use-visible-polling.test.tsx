import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useVisiblePolling } from "@/lib/use-visible-polling";

function deferred() {
  let resolve!: () => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<void>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useVisiblePolling", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("serializes ticks and coalesces reconnect events behind an active poll", async () => {
    vi.useFakeTimers();
    const first = deferred();
    const second = deferred();
    const poll = vi
      .fn()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise)
      .mockResolvedValue(undefined);

    renderHook(() => useVisiblePolling(poll, 1_000));
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(poll).toHaveBeenCalledTimes(1);

    await act(() => vi.advanceTimersByTimeAsync(10_000));
    act(() => window.dispatchEvent(new Event("online")));
    act(() => window.dispatchEvent(new Event("online")));
    expect(poll).toHaveBeenCalledTimes(1);

    await act(async () => {
      first.resolve();
      await first.promise;
    });
    expect(poll).toHaveBeenCalledTimes(2);

    await act(async () => {
      second.reject(new Error("offline"));
      await second.promise.catch(() => undefined);
    });
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(poll).toHaveBeenCalledTimes(3);
  });
});
