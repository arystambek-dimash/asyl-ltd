import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { useCart, useCartStore } from "./cart";

const auth = vi.hoisted(() => ({ me: { id: 1 } as { id: number } | null }));
vi.mock("@/store/auth", () => ({ useAuth: () => auth }));

beforeEach(() => {
  auth.me = { id: 1 };
  useCartStore.setState({ carts: {} });
  localStorage.clear();
});

it("хранит корзину у каждого клиента отдельно и переживает перезагрузку", () => {
  const first = renderHook(() => useCart());
  act(() => {
    first.result.current.add(10);
    first.result.current.add(10);
    first.result.current.setCurrency("USD");
  });
  expect(first.result.current.count).toBe(2);
  expect(JSON.parse(localStorage.getItem("asyl_cart_v1") ?? "{}").state.carts["1"]).toEqual({
    lines: [{ product: 10, quantity: 2 }],
    currency: "USD",
  });

  auth.me = { id: 2 };
  const second = renderHook(() => useCart());
  expect(second.result.current.count).toBe(0);
});

it("без входа ничего не меняет", () => {
  auth.me = null;
  const { result } = renderHook(() => useCart());
  act(() => result.current.add(10));
  expect(useCartStore.getState().carts).toEqual({});
});
