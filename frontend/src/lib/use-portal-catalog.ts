"use client";
import type { PortalProduct } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { useCart } from "@/store/cart";

/** Прайс клиента в выбранной валюте корзины (без выбора — валюта из его карточки). */
export function usePortalCatalog() {
  const cart = useCart();
  const query = useApi<PortalProduct[]>(
    cart.currency ? `/portal/catalog/?currency=${cart.currency}` : "/portal/catalog/",
  );
  return {
    ...query,
    currency: cart.currency ?? query.data?.[0]?.currency ?? "KZT",
    setCurrency: cart.setCurrency,
  };
}
