"use client";
import { useState } from "react";
import Link from "next/link";
import { Plus, Store } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { ShopOrderForm } from "@/components/shop-orders/shop-order-form";
import { ShopOrderHistory } from "@/components/shop-orders/shop-order-history";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { EmptyRow, Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { formatCurrency, bagsLabel } from "@/lib/utils";
import type { ShopOrder, ShopProduct, ShopSummary } from "@/lib/types";
import { useAuth } from "@/store/auth";

export default function ShopOrdersPage() {
  return (
    <RequirePerm title="Заказы магазина" perm="orders.create">
      <ShopOrdersContent />
    </RequirePerm>
  );
}

function ShopOrdersContent() {
  const { me } = useAuth();
  const summary = useApi<ShopSummary>(me?.shop ? "/shop-orders/summary/" : null);
  const shop = summary.data;
  const catalog = useApi<ShopProduct[]>(shop ? "/shop-orders/catalog/" : null);
  const orders = usePagedApi<ShopOrder>(shop ? "/shop-orders/" : null, 20);
  const [creating, setCreating] = useState(false);

  function refresh() {
    void catalog.reload();
    void orders.reload();
  }

  if (!me?.shop || summary.errorStatus === 404) {
    return (
      <AppShell title="Заказы магазина">
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
            <Store className="size-8 text-[var(--muted-foreground)]" />
            <div className="font-medium">У вас нет своего магазина</div>
            <p className="text-sm text-[var(--muted-foreground)]">Его включает администратор в карточке сотрудника.</p>
          </CardContent>
        </Card>
      </AppShell>
    );
  }

  if (!shop) {
    return (
      <AppShell title="Заказы магазина">
        <DataGate loading={summary.loading} error={summary.error} onRetry={summary.reload} />
      </AppShell>
    );
  }

  return (
    <AppShell
      title="Заказы магазина"
      description={`${shop.name} · Продажи из собственного остатка магазина.`}
      actions={
        <Button onClick={() => setCreating(true)} disabled={!catalog.data}>
          <Plus className="size-4" /> Новый заказ
        </Button>
      }
    >
      <Card className="mb-5">
        <CardContent className="space-y-2 py-4">
          <div className="text-sm font-medium">Пополнение магазина</div>
          <p className="text-sm text-[var(--muted-foreground)]">
            Создайте обычный заказ в разделе «Заказы» и выберите получателя «{shop.name}». Товар поступит в магазин
            после подтверждения грузчиком со склада «{shop.warehouse_name}».
          </p>
          <Link href="/orders" className="inline-block text-sm font-medium underline underline-offset-4">
            Перейти к обычным заказам
          </Link>
        </CardContent>
      </Card>

      <h2 className="mb-3 text-base font-semibold">Остатки магазина</h2>
      {catalog.data ? (
        <Card className="mb-5">
          <Table>
            <THead>
              <TR>
                <TH>Товар</TH>
                <TH className="text-right">В наличии</TH>
                <TH className="text-right">Цена</TH>
              </TR>
            </THead>
            <TBody>
              {catalog.data.length === 0 && <EmptyRow colSpan={3}>Товаров пока нет</EmptyRow>}
              {catalog.data.map((product) => (
                <TR key={product.id}>
                  <TD>{product.label}</TD>
                  <TD className="text-right tabular-nums whitespace-nowrap">{bagsLabel(product.stock_bags)}</TD>
                  <TD className="text-right tabular-nums whitespace-nowrap">
                    {product.price == null ? "Нет цены" : formatCurrency(product.price, product.currency)}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </Card>
      ) : (
        <DataGate loading={catalog.loading} error={catalog.error} onRetry={catalog.reload} />
      )}
      {catalog.data && catalog.error && (
        <ErrorAlert message={catalog.error} onRetry={catalog.reload} className="mb-4" />
      )}

      <h2 className="mb-3 text-base font-semibold">История заказов</h2>
      {orders.error ? (
        <ErrorAlert message={orders.error} onRetry={orders.reload} />
      ) : (
        <ShopOrderHistory orders={orders} onChanged={refresh} />
      )}
      <ShopOrderForm
        open={creating}
        products={catalog.data ?? []}
        shopName={shop.name}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          refresh();
        }}
      />
    </AppShell>
  );
}
