"use client";
import Link from "next/link";
import { Boxes, ShoppingCart } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { CartBar } from "@/components/portal/cart-bar";
import { CurrencyToggle } from "@/components/portal/currency-toggle";
import { ProductCard } from "@/components/portal/product-card";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataGate } from "@/components/ui/data-state";
import { priceCart } from "@/lib/cart";
import { usePortalCatalog } from "@/lib/use-portal-catalog";
import { bagsLabel, cn } from "@/lib/utils";
import { useCart } from "@/store/cart";

export default function PortalCatalogPage() {
  const { data: products, loading, error, reload, currency, setCurrency } = usePortalCatalog();
  const cart = useCart();
  const priced = priceCart(cart.lines, products);
  return (
    <AppShell
      title="Товары"
      portal
      footer={<CartBar count={priced.quantity} total={priced.total} currency={currency} />}
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-medium">Ваш личный прайс-лист</p>
          <p className="text-xs text-[var(--muted-foreground)]">Цены закреплены специально для вашей компании</p>
        </div>
        <div className="flex items-center gap-2">
          <CurrencyToggle value={currency} onChange={setCurrency} />
          <Link href="/portal/cart" className={cn(buttonVariants({ size: "sm" }), "hidden md:inline-flex")}>
            <ShoppingCart className="size-4" />
            {priced.quantity > 0 ? `Корзина · ${bagsLabel(priced.quantity)}` : "Корзина"}
          </Link>
        </div>
      </div>
      {!products ? (
        <DataGate loading={loading} error={error} onRetry={reload} />
      ) : products.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-2 py-14 text-center">
            <Boxes className="size-8 text-[var(--muted-foreground)]" />
            <div className="text-sm font-medium">Товаров пока нет</div>
            <p className="max-w-sm text-xs text-[var(--muted-foreground)]">
              Как только менеджер добавит активные товары, они появятся здесь для заказа.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 xl:grid-cols-4">
          {products.map((product) => (
            <ProductCard key={product.id} product={product} />
          ))}
        </div>
      )}
    </AppShell>
  );
}
