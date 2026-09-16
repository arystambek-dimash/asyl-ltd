"use client";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { cartQuantity, withQuantity, type CartCurrency, type CartLine } from "@/lib/cart";
import { useAuth } from "@/store/auth";

interface ClientCart {
  lines: CartLine[];
  /** null — валюта клиента по умолчанию из его карточки. */
  currency: CartCurrency | null;
}

interface CartStore {
  carts: Record<string, ClientCart>;
  update: (owner: string, change: (cart: ClientCart) => ClientCart) => void;
}

const EMPTY_CART: ClientCart = { lines: [], currency: null };

// Корзина у каждого клиента своя: один телефон или ПК могут делить несколько кабинетов.
export const useCartStore = create<CartStore>()(
  persist(
    (set) => ({
      carts: {},
      update: (owner, change) =>
        set((state) => ({ carts: { ...state.carts, [owner]: change(state.carts[owner] ?? EMPTY_CART) } })),
    }),
    {
      name: "asyl_cart_v1",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ carts: state.carts }),
    },
  ),
);

/** Корзина текущего клиента: переживает переходы между страницами и перезагрузку. */
export function useCart() {
  const { me } = useAuth();
  const owner = me ? String(me.id) : "";
  const cart = useCartStore((state) => state.carts[owner] ?? EMPTY_CART);
  const update = useCartStore((state) => state.update);
  const change = (fn: (cart: ClientCart) => ClientCart) => {
    if (owner) update(owner, fn);
  };
  const quantityOf = (product: number) => cart.lines.find((line) => line.product === product)?.quantity ?? 0;

  return {
    lines: cart.lines,
    currency: cart.currency,
    count: cartQuantity(cart.lines),
    quantityOf,
    setQuantity: (product: number, quantity: number) =>
      change((current) => ({ ...current, lines: withQuantity(current.lines, product, quantity) })),
    // От текущего состояния, а не от отрисованного: быстрые нажатия не теряют мешки.
    add: (product: number) =>
      change((current) => {
        const quantity = current.lines.find((line) => line.product === product)?.quantity ?? 0;
        return { ...current, lines: withQuantity(current.lines, product, quantity + 1) };
      }),
    remove: (product: number) => change((current) => ({ ...current, lines: withQuantity(current.lines, product, 0) })),
    clear: () => change((current) => ({ ...current, lines: [] })),
    setCurrency: (currency: CartCurrency) => change((current) => ({ ...current, currency })),
  };
}
