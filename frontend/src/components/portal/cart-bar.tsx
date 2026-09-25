"use client";
import Link from "next/link";
import { ChevronRight, ShoppingCart } from "lucide-react";
import type { CartCurrency } from "@/lib/cart";
import { bagsLabel, formatCurrency } from "@/lib/utils";

/** Нижняя панель на телефоне: каталог можно листать дальше, а корзина всегда под пальцем. */
export function CartBar({ count, total, currency }: { count: number; total: number; currency: CartCurrency }) {
  if (count === 0) return null;
  return (
    <div className="border-t bg-[var(--card)] px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:hidden">
      <Link
        href="/portal/cart"
        className="flex h-12 items-center gap-3 rounded-xl bg-[var(--primary)] px-4 text-[var(--primary-foreground)]"
      >
        <ShoppingCart className="size-5 shrink-0" />
        <span className="flex-1 text-sm font-semibold">Корзина · {bagsLabel(count)}</span>
        {total > 0 && <span className="text-sm font-semibold tabular-nums">{formatCurrency(total, currency)}</span>}
        <ChevronRight className="size-4 shrink-0" />
      </Link>
    </div>
  );
}
