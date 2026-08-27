import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  delete: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: {
    get: mocks.get,
    post: mocks.post,
    delete: mocks.delete,
  },
  apiError: (cause: unknown) => (cause instanceof Error ? cause.message : String(cause)),
  isCanceledRequest: () => false,
}));

import { useAiCounter, type AiStatus } from "@/lib/use-ai-counter";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function response(status: AiStatus) {
  return { data: status };
}

async function settleMicrotasks() {
  await act(async () => {
    for (let index = 0; index < 5; index += 1) await Promise.resolve();
  });
}

describe("useAiCounter polling scope", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  afterEach(() => {
    Reflect.deleteProperty(document, "hidden");
    vi.useRealTimers();
  });

  it("waits for an active poll before scheduling the next one", async () => {
    const first = deferred<{ data: AiStatus }>();
    const second = deferred<{ data: AiStatus }>();
    mocks.get.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const { result, unmount } = renderHook(() => useAiCounter("cam1", 42, true));
    expect(mocks.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(mocks.get).toHaveBeenCalledTimes(1);

    first.resolve(response({ running: true, total: 1 }));
    await settleMicrotasks();
    expect(result.current.status?.total).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(499);
    });
    expect(mocks.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(mocks.get).toHaveBeenCalledTimes(2);

    unmount();
    second.resolve(response({ running: true, total: 2 }));
    await settleMicrotasks();
  });

  it("ignores a late poll response after the scope is deactivated", async () => {
    const oldPoll = deferred<{ data: AiStatus }>();
    mocks.get.mockReturnValueOnce(oldPoll.promise);
    const { result, rerender } = renderHook(({ active }) => useAiCounter("cam1", 42, active), {
      initialProps: { active: true },
    });

    expect(mocks.get).toHaveBeenCalledTimes(1);
    rerender({ active: false });
    expect(result.current.status).toBeNull();

    oldPoll.resolve(response({ running: true, total: 99 }));
    await settleMicrotasks();

    expect(result.current.status).toBeNull();
    expect(result.current.running).toBe(false);
    expect(mocks.get).toHaveBeenCalledTimes(1);
  });

  it("does not let an old camera response overwrite the new scope", async () => {
    const oldPoll = deferred<{ data: AiStatus }>();
    const newPoll = deferred<{ data: AiStatus }>();
    mocks.get.mockReturnValueOnce(oldPoll.promise).mockReturnValueOnce(newPoll.promise);
    const { result, rerender } = renderHook(({ cam }) => useAiCounter(cam, 42, true), {
      initialProps: { cam: "cam1" },
    });

    rerender({ cam: "cam2" });
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(mocks.get.mock.calls[1]?.[0]).toBe("/cameras/cam2/ai/?order_id=42");

    newPoll.resolve(response({ running: true, total: 2 }));
    await settleMicrotasks();
    expect(result.current.status?.total).toBe(2);

    oldPoll.resolve(response({ running: true, total: 1 }));
    await settleMicrotasks();

    expect(result.current.status?.total).toBe(2);
  });

  it("keeps the last live status and cadence after a transient poll failure", async () => {
    mocks.get
      .mockResolvedValueOnce(response({ running: true, total: 7 }))
      .mockRejectedValueOnce(new Error("temporary network failure"))
      .mockResolvedValueOnce(response({ running: true, total: 8 }));

    const { result } = renderHook(() => useAiCounter("cam1", 42, true));
    await settleMicrotasks();
    expect(result.current.status?.total).toBe(7);
    expect(result.current.stale).toBe(false);

    await act(() => vi.advanceTimersByTimeAsync(500));
    expect(mocks.get).toHaveBeenCalledTimes(2);
    expect(result.current.status?.total).toBe(7);
    expect(result.current.running).toBe(true);
    expect(result.current.stale).toBe(true);
    expect(result.current.error).toBe("");

    await act(() => vi.advanceTimersByTimeAsync(499));
    expect(mocks.get).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(mocks.get).toHaveBeenCalledTimes(3);
    expect(result.current.status?.total).toBe(8);
    expect(result.current.stale).toBe(false);
  });

  it("surfaces a poll error only when no last-good status exists", async () => {
    mocks.get.mockRejectedValueOnce(new Error("AI service is unreachable"));

    const { result } = renderHook(() => useAiCounter("cam1", 42, true));
    await settleMicrotasks();

    expect(result.current.status).toBeNull();
    expect(result.current.running).toBe(false);
    expect(result.current.stale).toBe(false);
    expect(result.current.error).toBe("AI service is unreachable");
  });
});

describe("useAiCounter command ordering", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
  });

  afterEach(() => {
    Reflect.deleteProperty(document, "hidden");
    vi.useRealTimers();
  });

  it("serializes commands and lets the latest action own the visible result", async () => {
    const startRequest = deferred<{ data: AiStatus }>();
    const stopRequest = deferred<{ data: AiStatus }>();
    mocks.get.mockResolvedValueOnce(response({ running: false, total: 0 }));
    mocks.post.mockReturnValueOnce(startRequest.promise);
    mocks.delete.mockReturnValueOnce(stopRequest.promise);

    const { result } = renderHook(() => useAiCounter("cam1", 42, true));
    await settleMicrotasks();

    let startPromise!: Promise<void>;
    let stopPromise!: Promise<void>;
    act(() => {
      startPromise = result.current.start();
      stopPromise = result.current.stop();
    });

    await settleMicrotasks();
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.delete).not.toHaveBeenCalled();
    expect(result.current.busy).toBe(true);

    startRequest.resolve(response({ running: true, total: 5 }));
    await settleMicrotasks();
    expect(mocks.delete).toHaveBeenCalledTimes(1);
    expect(result.current.status).toMatchObject({ running: false, total: 0 });
    expect(result.current.busy).toBe(true);

    stopRequest.resolve(response({ running: false, total: 5 }));
    await act(async () => {
      await Promise.all([startPromise, stopPromise]);
    });

    expect(result.current.status).toMatchObject({ running: false, total: 5 });
    expect(result.current.running).toBe(false);
    expect(result.current.busy).toBe(false);
  });

  it("does not let a late command from an old camera overwrite or block a new scope", async () => {
    const oldStart = deferred<{ data: AiStatus }>();
    const newReset = deferred<{ data: AiStatus }>();
    mocks.get
      .mockResolvedValueOnce(response({ running: false, total: 0 }))
      .mockResolvedValueOnce(response({ running: true, total: 20 }));
    mocks.post.mockReturnValueOnce(oldStart.promise).mockReturnValueOnce(newReset.promise);

    const { result, rerender } = renderHook(({ cam }) => useAiCounter(cam, 42, true), {
      initialProps: { cam: "cam1" },
    });
    await settleMicrotasks();

    let oldStartPromise!: Promise<void>;
    act(() => {
      oldStartPromise = result.current.start();
    });
    await settleMicrotasks();

    rerender({ cam: "cam2" });
    await settleMicrotasks();
    expect(result.current.status).toMatchObject({ running: true, total: 20 });

    let newResetPromise!: Promise<void>;
    act(() => {
      newResetPromise = result.current.reset();
    });
    await settleMicrotasks();
    expect(mocks.post).toHaveBeenCalledTimes(2);
    expect(mocks.post.mock.calls[1]?.[0]).toBe("/cameras/cam2/ai/reset/");

    newReset.resolve(response({ running: true, total: 0 }));
    await act(async () => {
      await newResetPromise;
    });
    expect(result.current.status).toMatchObject({ running: true, total: 0 });

    oldStart.resolve(response({ running: true, total: 99 }));
    await act(async () => {
      await oldStartPromise;
    });
    expect(result.current.status).toMatchObject({ running: true, total: 0 });
    expect(result.current.busy).toBe(false);
  });
});
