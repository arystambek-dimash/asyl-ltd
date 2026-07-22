import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useApi } from "@/lib/use-api";

const getMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { get: getMock },
  apiError: () => "request failed",
  isCanceledRequest: () => false,
}));

interface Payload {
  id: string;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function resolveRequest<T>(request: ReturnType<typeof deferred<{ data: T }>>, data: T) {
  await act(async () => {
    request.resolve({ data });
    await request.promise;
  });
}

describe("useApi", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("ignores a deferred response from the previous URL", async () => {
    const first = deferred<{ data: Payload }>();
    const second = deferred<{ data: Payload }>();
    getMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

    const { result, rerender } = renderHook(({ url }: { url: string }) => useApi<Payload>(url), {
      initialProps: { url: "/first/" },
    });

    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    rerender({ url: "/second/" });
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));

    await resolveRequest(first, { id: "stale" });
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(true);

    await resolveRequest(second, { id: "current" });
    expect(result.current.data).toEqual({ id: "current" });
    expect(result.current.loading).toBe(false);
  });

  it("keeps current data visible while reloading the same URL", async () => {
    const initial = deferred<{ data: Payload }>();
    const reload = deferred<{ data: Payload }>();
    getMock.mockReturnValueOnce(initial.promise).mockReturnValueOnce(reload.promise);

    const { result } = renderHook(() => useApi<Payload>("/orders/"));
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    await resolveRequest(initial, { id: "existing" });
    expect(result.current.data).toEqual({ id: "existing" });

    act(() => {
      void result.current.reload();
    });
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));

    expect(result.current.loading).toBe(true);
    expect(result.current.data).toEqual({ id: "existing" });

    await resolveRequest(reload, { id: "updated" });
    expect(result.current.data).toEqual({ id: "updated" });
    expect(result.current.loading).toBe(false);
  });
});
