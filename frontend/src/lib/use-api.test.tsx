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

async function resolveRequest<T>(request: PromiseWithResolvers<{ data: T }>, data: T) {
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
    const first = Promise.withResolvers<{ data: Payload }>();
    const second = Promise.withResolvers<{ data: Payload }>();
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
    const initial = Promise.withResolvers<{ data: Payload }>();
    const reload = Promise.withResolvers<{ data: Payload }>();
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

  it("refresh() polls silently: no loading flag, a failure keeps the last data", async () => {
    getMock.mockResolvedValueOnce({ data: { id: "existing" } });
    const { result } = renderHook(() => useApi<Payload>("/status/"));
    await waitFor(() => expect(result.current.data).toEqual({ id: "existing" }));

    const poll = Promise.withResolvers<{ data: Payload }>();
    getMock.mockReturnValueOnce(poll.promise);
    act(() => {
      void result.current.refresh();
    });
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));
    expect(result.current.loading).toBe(false);
    await resolveRequest(poll, { id: "polled" });
    expect(result.current.data).toEqual({ id: "polled" });

    getMock.mockRejectedValueOnce(new Error("offline"));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.data).toEqual({ id: "polled" });
    expect(result.current.error).toBe("");
    expect(result.current.loading).toBe(false);
  });

  it("keeps a saved value when an older request lands afterwards", async () => {
    // Фоновый опрос стартует до записи и отвечает после неё. Без этого
    // прежнее состояние затирало только что сохранённые настройки, и на
    // экране выбор «слетал» обратно.
    const inFlight = Promise.withResolvers<{ data: Payload }>();
    const polling = Promise.withResolvers<{ data: Payload }>();
    getMock.mockReturnValueOnce(inFlight.promise).mockReturnValueOnce(polling.promise);

    const { result } = renderHook(() => useApi<Payload>("/cameras/always-on-settings/"));
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(1));
    await resolveRequest(inFlight, { id: "before-save" });

    act(() => {
      void result.current.reload();
    });
    await waitFor(() => expect(getMock).toHaveBeenCalledTimes(2));

    act(() => {
      result.current.setData({ id: "saved" });
    });
    expect(result.current.data).toEqual({ id: "saved" });
    expect(result.current.loading).toBe(false);

    await resolveRequest(polling, { id: "before-save" });
    expect(result.current.data).toEqual({ id: "saved" });
  });

  it("exposes the response status for access errors", async () => {
    getMock.mockRejectedValueOnce({ response: { status: 403 } });

    const { result } = renderHook(() => useApi<Payload>("/shipping-sessions/"));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBe("request failed");
    expect(result.current.errorStatus).toBe(403);
  });
});
