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

  it("тихое обновление перечитывает показанные страницы без индикатора загрузки", async () => {
    getMock.mockResolvedValueOnce(pageResponse([1, 2], 3, "next"));
    const { result } = renderHook(() => usePagedApi<Row>("/loader/queue/", 2));
    await waitFor(() => expect(result.current.items.length).toBe(2));
    getMock.mockResolvedValueOnce(pageResponse([3], 3, null));
    await act(async () => {
      result.current.loadMore();
    });
    await waitFor(() => expect(result.current.items.length).toBe(3));

    getMock.mockResolvedValueOnce(pageResponse([2, 4], 3, "next")).mockResolvedValueOnce(pageResponse([5], 3, null));
    const loadingSeen: boolean[] = [];
    let refresh!: Promise<void>;
    act(() => {
      refresh = result.current.refresh();
      loadingSeen.push(result.current.loading);
    });
    await act(async () => {
      await refresh;
    });

    expect(loadingSeen).toEqual([false]);
    expect(result.current.loading).toBe(false);
    expect(getMock.mock.calls.slice(-2).map(([url]) => url)).toEqual([
      "/loader/queue/?page=1&page_size=2",
      "/loader/queue/?page=2&page_size=2",
    ]);
    expect(result.current.items.map((r) => r.id)).toEqual([2, 4, 5]);
    expect(result.current.refreshError).toBe("");
  });

  it("ошибка тихого обновления не заменяет список", async () => {
    getMock.mockResolvedValueOnce(pageResponse([1, 2], 2, null));
    const { result } = renderHook(() => usePagedApi<Row>("/loader/queue/", 10));
    await waitFor(() => expect(result.current.items.length).toBe(2));

    getMock.mockRejectedValueOnce(new Error("offline"));
    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.items.map((r) => r.id)).toEqual([1, 2]);
    expect(result.current.error).toBe("");
    expect(result.current.refreshError).toBe("request failed");

    getMock.mockResolvedValueOnce(pageResponse([1], 1, null));
    await act(async () => {
      await result.current.refresh();
    });
    expect(result.current.refreshError).toBe("");
    expect(result.current.items.map((r) => r.id)).toEqual([1]);
  });

  it("ответ своей мутации применяется к списку и гасит устаревший опрос", async () => {
    getMock.mockResolvedValueOnce(pageResponse([1, 2], 2, null));
    const { result } = renderHook(() => usePagedApi<Row>("/loader/queue/", 10));
    await waitFor(() => expect(result.current.items.length).toBe(2));

    // Опрос ушёл до отгрузки и вернёт строку, которой уже нет в очереди.
    let resolveStale!: (value: unknown) => void;
    getMock.mockReturnValueOnce(new Promise((resolve) => (resolveStale = resolve)));
    let refresh!: Promise<void>;
    act(() => {
      refresh = result.current.refresh();
    });
    act(() => result.current.applyItems((rows) => rows.filter((row) => row.id !== 1)));
    await act(async () => {
      resolveStale(pageResponse([1, 2], 2, null));
      await refresh;
    });

    expect(result.current.items.map((r) => r.id)).toEqual([2]);
    expect(result.current.count).toBe(1);
  });

  it("ответ мутации не отменяет загрузку нового фильтра — она повторяется", async () => {
    getMock.mockResolvedValueOnce(pageResponse([1, 2], 2, null));
    const { result, rerender } = renderHook(({ url }: { url: string }) => usePagedApi<Row>(url, 10), {
      initialProps: { url: "/loader/queue/?day=1" },
    });
    await waitFor(() => expect(result.current.items.length).toBe(2));

    // Фильтр сменился, первая страница ещё в пути — и тут пришёл ответ мутации.
    let resolveFirst!: (value: unknown) => void;
    getMock.mockReturnValueOnce(new Promise((resolve) => (resolveFirst = resolve)));
    rerender({ url: "/loader/queue/?day=2" });
    getMock.mockResolvedValueOnce(pageResponse([7, 8], 2, null));
    act(() => result.current.applyItems((rows) => rows.filter((row) => row.id !== 1)));
    await act(async () => {
      resolveFirst(pageResponse([5], 1, null));
    });

    await waitFor(() => expect(result.current.items.map((r) => r.id)).toEqual([7, 8]));
    expect(result.current.loading).toBe(false);
    expect(result.current.count).toBe(2);
    expect(getMock.mock.calls.at(-1)![0]).toBe("/loader/queue/?day=2&page=1&page_size=10");
  });
});
