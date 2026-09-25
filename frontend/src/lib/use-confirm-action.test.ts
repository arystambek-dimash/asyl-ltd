import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useConfirmAction } from "@/lib/use-confirm-action";

type Row = { id: number };

describe("useConfirmAction", () => {
  it("после успеха закрывает окно", async () => {
    const run = vi.fn().mockResolvedValue(undefined);
    const { result } = renderHook(() => useConfirmAction<Row>(run));

    act(() => result.current.open({ id: 7 }));
    expect(result.current.dialog.open).toBe(true);

    await act(() => result.current.confirm());

    expect(run).toHaveBeenCalledWith({ id: 7 });
    expect(result.current.item).toBeNull();
    expect(result.current.busy).toBe(false);
    expect(result.current.error).toBe("");
  });

  it("при ошибке оставляет окно открытым с причиной, повторное открытие её сбрасывает", async () => {
    const run = vi.fn().mockRejectedValue({ response: { status: 409, data: { detail: "Есть заказы" } } });
    const { result } = renderHook(() => useConfirmAction<Row>(run));

    act(() => result.current.open({ id: 3 }));
    await act(() => result.current.confirm());

    expect(result.current.item).toEqual({ id: 3 });
    expect(result.current.error).toBe("Есть заказы");
    expect(result.current.busy).toBe(false);

    act(() => result.current.close());
    act(() => result.current.open({ id: 4 }));
    expect(result.current.error).toBe("");
  });

  it("не закрывается и не запускает действие повторно, пока запрос идёт", async () => {
    let resolve: () => void = () => undefined;
    const run = vi.fn(() => new Promise<void>((done) => (resolve = done)));
    const { result } = renderHook(() => useConfirmAction<Row>(run));

    act(() => result.current.open({ id: 1 }));
    let pending: Promise<void> = Promise.resolve();
    act(() => {
      pending = result.current.confirm();
    });
    expect(result.current.busy).toBe(true);

    act(() => result.current.close());
    await act(() => result.current.confirm());
    expect(result.current.item).toEqual({ id: 1 });
    expect(run).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolve();
      await pending;
    });
    expect(result.current.item).toBeNull();
  });
});
