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
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
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
      await vi.advanceTimersByTimeAsync(1_499);
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
});
