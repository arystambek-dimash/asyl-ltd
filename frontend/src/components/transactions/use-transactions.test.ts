import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Payment } from "@/lib/types";
import { useTransactions } from "./use-transactions";

const postMock = vi.hoisted(() => vi.fn());
const useApiMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  api: { post: postMock },
  apiError: (error: unknown) => (error instanceof Error ? error.message : "Ошибка"),
  blobApiError: async () => "Ошибка",
}));
vi.mock("@/lib/use-api", () => ({ useApi: useApiMock }));

const payment = { id: 7, order: 3, currency: "KZT", amount: "5000.00", status: "confirmed" } as Payment;

beforeEach(() => {
  postMock.mockReset();
  useApiMock.mockReset();
  useApiMock.mockReturnValue({ data: null, loading: false, error: "", reload: vi.fn(async () => undefined) });
});

describe("useTransactions", () => {
  it("keeps a reopen error inside its dialog instead of repeating it on the page", async () => {
    postMock.mockRejectedValue(new Error("Нельзя вернуть"));
    const { result } = renderHook(() => useTransactions());

    act(() => result.current.open("reopen", payment));
    await act(() => result.current.reopen(payment));

    expect(result.current.error).toBe("Нельзя вернуть");
    expect(result.current.pageError).toBe("");
  });

  it("shows an error on the page while no action dialog is open", async () => {
    postMock.mockRejectedValue(new Error("Счёт не отправлен"));
    const { result } = renderHook(() => useTransactions());

    await act(() => result.current.issue(payment));

    expect(result.current.pageError).toBe("Счёт не отправлен");
  });

  it("sends one issue request on a double tap", async () => {
    let finish: (value: unknown) => void = () => {};
    postMock.mockReturnValue(new Promise((resolve) => (finish = resolve)));
    const { result } = renderHook(() => useTransactions());

    let first: Promise<void> = Promise.resolve();
    act(() => {
      first = result.current.issue(payment);
      void result.current.issue(payment);
    });
    await act(async () => {
      finish({ data: payment });
      await first;
    });

    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it("does not release the guard of a running mutation when issue is tapped", async () => {
    let finish: (value: unknown) => void = () => {};
    postMock.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    postMock.mockResolvedValue({ data: payment });
    const { result } = renderHook(() => useTransactions());

    act(() => result.current.open("reopen", payment));
    let reopening: Promise<void> = Promise.resolve();
    act(() => {
      reopening = result.current.reopen(payment);
    });
    await act(() => result.current.issue(payment));
    await act(() => result.current.reopen(payment));
    await act(async () => {
      finish({});
      await reopening;
    });

    expect(postMock).toHaveBeenCalledTimes(1);
  });

  it("replaces the status sheet with the Kaspi QR after sending the invoice from it", async () => {
    const qrPayment = { ...payment, provider: { channel: "qr" } } as Payment;
    postMock.mockResolvedValue({ data: qrPayment });
    const { result } = renderHook(() => useTransactions());

    act(() => result.current.open("status", payment));
    await act(() => result.current.issue(payment));

    expect(result.current.dialog).toEqual({ kind: "qr", payment: qrPayment });
  });

  it("keeps the totals while the next page is loading", () => {
    const summary = {
      paid_by_currency: { KZT: "5000.00", USD: "0.00" },
      refunded_by_currency: { KZT: "0.00", USD: "0.00" },
      paid_by_method: {},
    };
    const firstPage = { results: [payment], count: 60, page: 1, pages: 2, summary };
    // Первая страница приходит после монтирования; пока грузится следующая,
    // useApi зануляет data.
    let loaded = false;
    useApiMock.mockImplementation((url: string) => {
      const ready = loaded && url.includes("page=1&");
      return { data: ready ? firstPage : null, loading: !ready, error: "", reload: vi.fn(async () => undefined) };
    });
    const { result, rerender } = renderHook(() => useTransactions());
    loaded = true;
    rerender();

    act(() => result.current.loadNextPage());

    expect(result.current.page).toBe(2);
    expect(result.current.meta).toMatchObject({ count: 60, summary });
    expect(result.current.rows).toEqual([payment]);
  });
});
