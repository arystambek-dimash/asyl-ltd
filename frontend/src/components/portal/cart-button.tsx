"use client";
import Link from "next/link";
import { ShoppingCart } from "lucide-react";
import { pluralRu } from "@/lib/utils";
import { useCart } from "@/store/cart";

export const bagsLabel = (count: number) => `${count} ${pluralRu(count, ["мешок", "мешка", "мешков"])}`;

/** Иконка корзины в шапке кабинета с числом мешков. */
export function CartButton() {
  const { count } = useCart();
  return (
    <Link
      href="/portal/cart"
      aria-label={count > 0 ? `Корзина: ${bagsLabel(count)}` : "Корзина"}
      className="relative text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
    >
      <ShoppingCart className="size-5" />
      {count > 0 && (
        <span className="absolute -right-1.5 -top-1.5 flex min-w-4 items-center justify-center rounded-full bg-[var(--primary)] px-1 text-[10px] font-semibold leading-4 text-[var(--primary-foreground)] tabular-nums">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Link>
  );
}
