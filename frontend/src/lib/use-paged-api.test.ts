import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { usePagedApi } from "@/lib/use-paged-api";

const getMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  api: { get: getMock },
  apiError: () => "request failed",
  isCanceledRequest: () => false,
}));

interface Row {
  id: number;
}

function pageResponse(ids: number[], count: number, next: string | null) {
  return { data: { count, next, previous: null, results: ids.map((id) => ({ id })) } };
}

describe("usePagedApi", () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it("грузит первую страницу и докладывает следующие в конец", async () => {
    getMock.mockResolvedValueOnce(pageResponse([1, 2], 3, "next-url"));
    const { result } = renderHook(() => usePagedApi<Row>("/orders/", 2));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(getMock).toHaveBeenLastCalledWith("/orders/?page=1&page_size=2", expect.anything());
    expect(result.current.items.map((r) => r.id)).toEqual([1, 2]);
    expect(result.current.count).toBe(3);
    expect(result.current.hasMore).toBe(true);

    getMock.mockResolvedValueOnce(pageResponse([3], 3, null));
    await act(async () => {
      result.current.loadMore();
    });
    await waitFor(() => expect(result.current.loadingMore).toBe(false));
    expect(getMock).toHaveBeenLastCalledWith("/orders/?page=2&page_size=2", expect.anything());
    expect(result.current.items.map((r) => r.id)).toEqual([1, 2, 3]);
    expect(result.current.hasMore).toBe(false);
  });

  it("сохраняет параметры запроса из базового URL", async () => {
    getMock.mockResolvedValueOnce(pageResponse([1], 1, null));
    const { result } = renderHook(() => usePagedApi<Row>("/orders/?status=pending", 10));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(getMock).toHaveBeenLastCalledWith("/orders/?status=pending&page=1&page_size=10", expect.anything());
  });

  it("смена URL сбрасывает накопленное и начинается с первой страницы", async () => {
    getMock.mockResolvedValueOnce(pageResponse([1, 2], 4, "next"));
    const { result, rerender } = renderHook(({ url }: { url: string | null }) => usePagedApi<Row>(url, 2), {
      initialProps: { url: "/orders/?d=1" as string | null },
    });
    await waitFor(() => expect(result.current.items.length).toBe(2));

    getMock.mockResolvedValueOnce(pageResponse([9], 1, null));
    rerender({ url: "/orders/?d=2" });
    await waitFor(() => expect(result.current.items.map((r) => r.id)).toEqual([9]));
    expect(result.current.count).toBe(1);
  });

  it("null-URL не грузит ничего", async () => {
    const { result } = renderHook(() => usePagedApi<Row>(null, 10));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(getMock).not.toHaveBeenCalled();
    expect(result.current.items).toEqual([]);
  });
});
