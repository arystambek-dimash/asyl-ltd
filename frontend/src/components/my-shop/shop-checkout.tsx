"use client";
import { useState } from "react";
import { PackageCheck, ShoppingCart, Trash2 } from "lucide-react";
import { ProductPhoto } from "@/components/catalog/product-photo";
import { QuantityStepper } from "@/components/portal/quantity-stepper";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import type { PricedCart } from "@/lib/cart";
import type { MyShopOrder } from "@/lib/types";
import { bagsLabel, formatCurrency } from "@/lib/utils";
import { useCart } from "@/store/cart";

/**
 * Нижняя панель корзины «Моего магазина» и окно «Списать со склада».
 * Заказ не ждёт ни менеджера, ни грузчика: после подтверждения мешки уже списаны.
 */
export function ShopCheckout({
  priced,
  currency,
  warehouseName,
  onPlaced,
}: {
  priced: PricedCart;
  currency: string;
  warehouseName: string;
  onPlaced: (order: MyShopOrder) => void;
}) {
  const cart = useCart();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function place() {
    if (busy || priced.orderItems.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<MyShopOrder>("/my-shop/orders/", { items: priced.orderItems });
      cart.clear();
      setOpen(false);
      onPlaced(data);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (priced.quantity === 0) return null;
  const rows = priced.rows.filter((row) => row.product);
  return (
    <>
      <div className="border-t bg-[var(--card)] px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <div className="mx-auto flex max-w-5xl items-center gap-3">
          <ShoppingCart className="size-5 shrink-0 text-[var(--muted-foreground)]" />
          <div className="min-w-0 flex-1">
            <div className="text-xs text-[var(--muted-foreground)]">{bagsLabel(priced.quantity)}</div>
            <div className="truncate font-semibold tabular-nums">{formatCurrency(priced.total, currency)}</div>
          </div>
          <Button className="h-11 px-5" onClick={() => setOpen(true)}>
            Оформить
          </Button>
        </div>
      </div>

      <Modal
        open={open}
        onClose={() => !busy && setOpen(false)}
        variant="sheet"
        eyebrow="Мой магазин"
        title="Списать со склада"
        description={`Мешки сразу спишутся со склада «${warehouseName}», без грузчика. Сумма ляжет в долг магазина — оплата в кассе.`}
        className="max-w-xl"
        footer={
          <>
            <Button type="button" variant="outline" disabled={busy} onClick={() => setOpen(false)}>
              Назад
            </Button>
            <Button type="button" disabled={busy || priced.orderItems.length === 0} onClick={place}>
              <PackageCheck className="size-4" />
              {busy ? "Списываем…" : `Списать ${bagsLabel(priced.quantity)}`}
            </Button>
          </>
        }
      >
        <ul className="-mx-1 divide-y">
          {rows.map(({ line, product, total }) =>
            product ? (
              <li key={line.product} className="flex items-center gap-3 px-1 py-3">
                <ProductPhoto
                  url={product.photo_url}
                  alt={product.label}
                  className="size-12 shrink-0 rounded-lg"
                  iconClassName="size-5"
                />
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium leading-snug">{product.label}</div>
                  <div className="mt-0.5 text-xs tabular-nums text-[var(--muted-foreground)]">
                    {total != null ? formatCurrency(total, currency) : "—"}
                  </div>
                </div>
                <QuantityStepper
                  value={line.quantity}
                  onChange={(quantity) => cart.setQuantity(product.id, quantity)}
                  label={product.label}
                  className="w-32 shrink-0"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className="shrink-0 text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                  aria-label={`Убрать: ${product.label}`}
                  disabled={busy}
                  onClick={() => cart.remove(product.id)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </li>
            ) : null,
          )}
        </ul>
        <div className="mt-3 flex items-baseline justify-between border-t pt-3">
          <span className="text-sm text-[var(--muted-foreground)]">Итого · {bagsLabel(priced.quantity)}</span>
          <span className="text-xl font-semibold tabular-nums">{formatCurrency(priced.total, currency)}</span>
        </div>
        <FormError message={error} className="mt-3" />
      </Modal>
    </>
  );
}
