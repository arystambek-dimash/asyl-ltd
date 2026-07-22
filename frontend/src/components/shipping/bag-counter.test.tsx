import { createRef } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Order } from "@/lib/types";
import { BagCounter, type BagCounterHandle } from "./bag-counter";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function order(bagsLoaded = 0): Order {
  return {
    id: 1,
    client: 1,
    currency: "KZT",
    status: "loading",
    truck_number: "",
    items: [{ product: 1, quantity: 20 }],
    total_amount: "0",
    paid_total: "0",
    is_fully_paid: false,
    debt_override: false,
    bags_loaded: bagsLoaded,
    created_at: "2026-07-22T00:00:00+05:00",
  };
}

async function advance(milliseconds: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds);
  });
}

async function settleMicrotasks() {
  await act(async () => {
    for (let index = 0; index < 5; index += 1) await Promise.resolve();
  });
}

describe("BagCounter persistence", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("serializes saves and keeps only the latest queued value", async () => {
    const first = deferred<void>();
    const second = deferred<void>();
    const requests = [first, second];
    let activeSaves = 0;
    let maxActiveSaves = 0;
    const onSave = vi.fn(() => {
      const request = requests.shift();
      if (!request) throw new Error("Unexpected save");
      activeSaves += 1;
      maxActiveSaves = Math.max(maxActiveSaves, activeSaves);
      return request.promise.finally(() => {
        activeSaves -= 1;
      });
    });

    render(<BagCounter order={order()} onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "Плюс один мешок" }));
    await advance(700);
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenLastCalledWith(1);

    fireEvent.click(screen.getByRole("button", { name: "Плюс пять мешков" }));
    await advance(700);
    fireEvent.click(screen.getByRole("button", { name: "Плюс один мешок" }));
    await advance(700);

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(maxActiveSaves).toBe(1);

    first.resolve();
    await settleMicrotasks();

    expect(onSave).toHaveBeenCalledTimes(2);
    expect(onSave).toHaveBeenLastCalledWith(7);
    expect(maxActiveSaves).toBe(1);

    second.resolve();
    await settleMicrotasks();
  });

  it("flushes a pending value on unmount after the active save settles", async () => {
    const first = deferred<void>();
    const onSave = vi.fn().mockReturnValueOnce(first.promise).mockResolvedValueOnce(undefined);
    const view = render(<BagCounter order={order()} onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "Плюс один мешок" }));
    await advance(700);
    expect(onSave).toHaveBeenLastCalledWith(1);

    fireEvent.click(screen.getByRole("button", { name: "Плюс пять мешков" }));
    view.unmount();
    await settleMicrotasks();
    expect(onSave).toHaveBeenCalledTimes(1);

    first.resolve();
    await settleMicrotasks();

    expect(onSave).toHaveBeenCalledTimes(2);
    expect(onSave).toHaveBeenLastCalledWith(6);
    await advance(1_000);
    expect(onSave).toHaveBeenCalledTimes(2);
  });

  it("lets completion await the debounced value and blocks on a failed save", async () => {
    const ref = createRef<BagCounterHandle>();
    const onSave = vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(undefined);
    render(<BagCounter ref={ref} order={order()} onSave={onSave} />);

    fireEvent.click(screen.getByRole("button", { name: "Плюс пять мешков" }));
    await expect(act(() => ref.current!.saveNow())).rejects.toThrow("offline");
    await settleMicrotasks();
    expect(onSave).toHaveBeenCalledWith(5);
    expect(screen.getByRole("alert")).toHaveTextContent("Произошла ошибка");

    let savedBags = 0;
    await act(async () => {
      savedBags = await ref.current!.saveNow();
    });
    expect(savedBags).toBe(5);
    expect(onSave).toHaveBeenCalledTimes(2);
    expect(onSave).toHaveBeenLastCalledWith(5);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
