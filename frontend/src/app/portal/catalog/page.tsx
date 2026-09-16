"use client";
import Link from "next/link";
import { Boxes, ShoppingCart } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { ProductPhoto } from "@/components/catalog/product-photo";
import { AddToCart } from "@/components/portal/add-to-cart";
import { CartBar } from "@/components/portal/cart-bar";
import { bagsLabel } from "@/components/portal/cart-button";
import { CurrencyToggle } from "@/components/portal/currency-toggle";
import { buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataGate } from "@/components/ui/data-state";
import { priceCart } from "@/lib/cart";
import { usePortalCatalog } from "@/lib/use-portal-catalog";
import { cn, formatCurrency } from "@/lib/utils";
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
          {products.map((p) => (
            <Card key={p.id} className="flex flex-col overflow-hidden">
              <ProductPhoto url={p.photo_url} alt={p.label} className="aspect-[4/3] w-full" iconClassName="size-10" />
              <div className="flex flex-1 flex-col gap-3 p-3 sm:p-4">
                <div className="flex-1">
                  <div className="text-sm font-medium leading-snug sm:text-[15px]">{p.label}</div>
                  <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">{Number(p.weight_kg)} кг / мешок</div>
                </div>
                {p.price ? (
                  <div className="text-lg font-semibold tabular-nums">
                    {formatCurrency(p.price, p.currency)}
                    <span className="ml-1 text-xs font-normal text-[var(--muted-foreground)]">/ мешок</span>
                  </div>
                ) : (
                  <div className="text-sm font-medium text-[var(--muted-foreground)]">Цена уточняется</div>
                )}
                <AddToCart product={p} />
              </div>
            </Card>
          ))}
        </div>
      )}
    </AppShell>
  );
}
