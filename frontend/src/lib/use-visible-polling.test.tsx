import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useVisiblePolling } from "@/lib/use-visible-polling";

describe("useVisiblePolling", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("serializes ticks and coalesces reconnect events behind an active poll", async () => {
    vi.useFakeTimers();
    const first = Promise.withResolvers<void>();
    const second = Promise.withResolvers<void>();
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

  it("polls immediately, aborts the in-flight poll and starts over on a new reset key", async () => {
    vi.useFakeTimers();
    const ticks: { signal: AbortSignal; first: boolean }[] = [];
    const poll = vi.fn((tick: { signal: AbortSignal; first: boolean }) => {
      ticks.push(tick);
      return new Promise<void>((resolve) => tick.signal.addEventListener("abort", () => resolve()));
    });

    const { rerender } = renderHook(
      ({ key }) => useVisiblePolling(poll, 1_000, true, { immediate: true, resetKey: key }),
      {
        initialProps: { key: "a" },
      },
    );
    expect(poll).toHaveBeenCalledTimes(1);
    expect(ticks[0].first).toBe(true);

    rerender({ key: "b" });
    expect(ticks[0].signal.aborted).toBe(true);
    expect(poll).toHaveBeenCalledTimes(2);
    expect(ticks[1]).toMatchObject({ first: true });
    expect(ticks[1].signal.aborted).toBe(false);
  });

  it("skips hidden-page ticks and reads a function interval before every tick", async () => {
    vi.useFakeTimers();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    let delay = 1_000;
    const poll = vi.fn(async (tick: { first: boolean }) => {
      delay = tick.first ? 5_000 : 1_000;
    });

    renderHook(() => useVisiblePolling(poll, () => delay, true, { immediate: true }));
    await act(() => vi.advanceTimersByTimeAsync(3_000));
    expect(poll).not.toHaveBeenCalled();

    hidden.mockReturnValue(false);
    await act(() => vi.advanceTimersByTimeAsync(1_000));
    expect(poll).toHaveBeenCalledTimes(1);
    expect(poll.mock.calls[0][0]).toMatchObject({ first: true });

    await act(() => vi.advanceTimersByTimeAsync(4_999));
    expect(poll).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(poll).toHaveBeenCalledTimes(2);
    expect(poll.mock.calls[1][0]).toMatchObject({ first: false });
    hidden.mockRestore();
  });
});
