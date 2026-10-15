"use client";
import { useMemo, useState } from "react";
import { ClipboardList, PackageOpen, Plus, ShoppingBasket, Store } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { ShopCheckout } from "@/components/my-shop/shop-checkout";
import { ShopPriceModal } from "@/components/my-shop/shop-price-modal";
import { ShopOrders } from "@/components/my-shop/shop-orders";
import { ProductCard } from "@/components/portal/product-card";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { StatCard } from "@/components/ui/stat-card";
import { Tabs } from "@/components/ui/tabs";
import { can } from "@/lib/can";
import { priceCart } from "@/lib/cart";
import { repeatLines } from "@/lib/my-shop";
import { showToast } from "@/lib/toast";
import type { MyShopOrder, MyShopSummary, PortalProduct } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { bagsLabel, pluralRu } from "@/lib/utils";
import { useAuth } from "@/store/auth";
import { useCart } from "@/store/cart";

type ShopTab = "order" | "orders";

/** «Мой магазин» сотрудника: заказ по прайсу магазина сразу со склада и свои заказы. */
export default function MyShopPage() {
  const summary = useApi<MyShopSummary>("/my-shop/");
  const shop = summary.data;
  const catalog = useApi<PortalProduct[]>(shop ? "/my-shop/catalog/" : null);
  const orders = usePagedApi<MyShopOrder>(shop ? "/my-shop/orders/" : null, 20);
  const cart = useCart();
  const { me } = useAuth();
  // С правом на цены сотрудник сам ставит прайс своему магазину (и заводит товар с правом на каталог).
  const canPrice = can(me, "clients.set_price");
  const [tab, setTab] = useState<ShopTab>("order");
  const [pricing, setPricing] = useState(false);

  // Заказать можно только товар с ценой: цену магазину ставит бухгалтер.
  const products = useMemo(() => (catalog.data ?? []).filter((product) => product.price), [catalog.data]);
  const unpriced = (catalog.data?.length ?? 0) - products.length;
  const priced = priceCart(cart.lines, catalog.data ? products : null);

  function refresh() {
    summary.refresh();
    orders.reload();
  }

  function placed(order: MyShopOrder) {
    showToast(`Заказ №${order.id}: ${bagsLabel(order.bags)} списано со склада`, "success");
    setTab("orders");
    refresh();
  }

  function priceSaved(label: string) {
    setPricing(false);
    showToast(`«${label}» — в прайсе магазина`, "success");
    catalog.reload();
  }

  function repeat(order: MyShopOrder) {
    const lines = repeatLines(order, new Set(products.map((product) => product.id)));
    if (lines.length === 0) {
      showToast("Этих товаров сейчас нет в прайсе магазина");
      return;
    }
    cart.replace(lines);
    setTab("order");
  }

  if (!shop) {
    return (
      <AppShell title="Мой магазин">
        {summary.errorStatus === 404 ? (
          <Card className="mx-auto max-w-md">
            <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
              <Store className="size-8 text-[var(--muted-foreground)]" />
              <div className="font-medium">У вас нет своего магазина</div>
              <p className="text-sm text-[var(--muted-foreground)]">
                Его включает администратор в карточке сотрудника.
              </p>
            </CardContent>
          </Card>
        ) : (
          <DataGate loading={summary.loading} error={summary.error} onRetry={summary.reload} />
        )}
      </AppShell>
    );
  }

  return (
    <AppShell
      title="Мой магазин"
      description={`${shop.name} · мешки списываются со склада «${shop.warehouse_name}» сразу, без грузчика.`}
      footer={
        tab === "order" && (
          <ShopCheckout
            priced={priced}
            currency={shop.currency}
            warehouseName={shop.warehouse_name}
            onPlaced={placed}
          />
        )
      }
    >
      <section className="mb-5 grid grid-cols-2 gap-3">
        <StatCard
          label="Долг магазина"
          tone={Number(shop.debt_total) > 0 ? "destructive" : undefined}
          value={
            <CurrencyAmounts byCurrency={shop.debt_by_currency} fallbackAmount={0} fallbackCurrency={shop.currency} />
          }
          caption="Оплата — в кассе"
        />
        <StatCard label="Взято в этом месяце" value={bagsLabel(shop.month_bags)}>
          <CurrencyAmounts
            byCurrency={shop.month_amounts}
            fallbackCurrency={shop.currency}
            empty={null}
            className="items-start text-xs text-[var(--muted-foreground)]"
          />
        </StatCard>
      </section>

      <Tabs
        className="mb-4"
        active={tab}
        onChange={(key) => setTab(key as ShopTab)}
        tabs={[
          { key: "order", label: "Заказать", icon: ShoppingBasket },
          { key: "orders", label: "Мои заказы", icon: ClipboardList, count: orders.count },
        ]}
      />

      {tab === "order" && canPrice && catalog.data && (
        <div className="mb-3 flex justify-end">
          <Button variant="outline" size="sm" onClick={() => setPricing(true)}>
            <Plus className="size-4" /> Товар в прайс
          </Button>
        </div>
      )}

      {tab === "order" ? (
        !catalog.data ? (
          <DataGate loading={catalog.loading} error={catalog.error} onRetry={catalog.reload} />
        ) : products.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-14 text-center">
              <PackageOpen className="size-8 text-[var(--muted-foreground)]" />
              <div className="text-sm font-medium">В прайсе магазина пока нет цен</div>
              <p className="max-w-sm text-xs text-[var(--muted-foreground)]">
                {canPrice
                  ? "Добавьте товар и его цену — он сразу появится здесь."
                  : "Цены вашему магазину ставит бухгалтер — как только поставит, товары появятся здесь."}
              </p>
            </CardContent>
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 sm:gap-4 xl:grid-cols-4">
              {products.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </div>
            {unpriced > 0 && (
              <p className="mt-4 text-xs text-[var(--muted-foreground)]">
                Ещё {unpriced} {pluralRu(unpriced, ["товар", "товара", "товаров"])} без цены —{" "}
                {canPrice ? "цену ставят через «Товар в прайс»." : "их цену ставит бухгалтер."}
              </p>
            )}
          </>
        )
      ) : orders.loading ? (
        <DataGate loading error="" />
      ) : orders.error ? (
        <ErrorAlert message={orders.error} onRetry={orders.reload} />
      ) : (
        <ShopOrders
          orders={orders.items}
          total={orders.count}
          hasMore={orders.hasMore}
          loadingMore={orders.loadingMore}
          onLoadMore={orders.loadMore}
          onRepeat={repeat}
          onChanged={refresh}
        />
      )}

      {pricing && catalog.data && (
        <ShopPriceModal
          clientId={shop.client}
          currency={shop.currency}
          catalog={catalog.data}
          onClose={() => setPricing(false)}
          onSaved={priceSaved}
        />
      )}
    </AppShell>
  );
}
