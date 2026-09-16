"use client";
import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, Info, ShoppingCart, Trash2 } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { ProductPhoto } from "@/components/catalog/product-photo";
import { bagsLabel } from "@/components/portal/cart-button";
import { CurrencyToggle } from "@/components/portal/currency-toggle";
import { QuantityStepper } from "@/components/portal/quantity-stepper";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataGate, ErrorAlert } from "@/components/ui/data-state";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { priceCart, type PricedCart } from "@/lib/cart";
import type { PortalOrder, Store } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePortalCatalog } from "@/lib/use-portal-catalog";
import { cn, formatCurrency, pluralRu } from "@/lib/utils";
import { useCart } from "@/store/cart";

type Transport = "truck" | "train";

const TRANSPORTS: [Transport, string][] = [
  ["truck", "🚚 Трак"],
  ["train", "🚃 Вагон"],
];

export default function PortalCartPage() {
  const cart = useCart();
  const catalog = usePortalCatalog();
  const { data: stores } = useApi<Store[]>("/portal/stores/");
  const [store, setStore] = useState("");
  const [transport, setTransport] = useState<Transport>("truck");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [placedOrder, setPlacedOrder] = useState<number | null>(null);
  const priced = priceCart(cart.lines, catalog.data);
  const currency = catalog.currency;

  async function checkout() {
    if (busy || priced.orderItems.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const { data } = await api.post<PortalOrder>("/portal/orders/", {
        items: priced.orderItems,
        currency,
        transport_type: transport,
        store: store ? Number(store) : null,
      });
      cart.clear();
      setPlacedOrder(data.id);
    } catch (cause) {
      setError(apiError(cause));
      // Товар мог закончиться, пока корзина лежала: обновим каталог, чтобы это было видно.
      void catalog.reload();
    } finally {
      setBusy(false);
    }
  }

  if (placedOrder !== null) {
    return (
      <AppShell title="Корзина" portal>
        <OrderPlaced orderId={placedOrder} />
      </AppShell>
    );
  }

  const canCheckout = priced.orderItems.length > 0 && !busy;
  return (
    <AppShell
      title="Корзина"
      portal
      footer={
        cart.lines.length > 0 && (
          <div className="flex items-center gap-3 border-t bg-[var(--card)] px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:hidden">
            <div className="min-w-0 flex-1">
              <div className="text-xs text-[var(--muted-foreground)]">{bagsLabel(priced.quantity)}</div>
              <div className="truncate font-semibold tabular-nums">{formatCurrency(priced.total, currency)}</div>
            </div>
            <Button className="h-11 px-5" disabled={!canCheckout} onClick={checkout}>
              {busy ? "Оформляем…" : "Оформить заказ"}
            </Button>
          </div>
        )
      }
    >
      {cart.lines.length === 0 ? (
        <EmptyCart />
      ) : !catalog.data ? (
        <DataGate loading={catalog.loading} error={catalog.error} onRetry={catalog.reload} />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_340px] lg:gap-6">
          <Card>
            <div className="flex items-center justify-between gap-3 border-b px-4 py-3 sm:px-5">
              <div className="font-semibold">
                {cart.lines.length} {pluralRu(cart.lines.length, ["позиция", "позиции", "позиций"])}
              </div>
              <Button variant="ghost" size="sm" onClick={cart.clear} disabled={busy}>
                <Trash2 className="size-4" /> Очистить
              </Button>
            </div>
            <ul className="divide-y">
              {priced.rows.map((row) => (
                <CartRow key={row.line.product} row={row} currency={currency} disabled={busy} />
              ))}
            </ul>
          </Card>

          <Card className="lg:sticky lg:top-0">
            <CardContent className="flex flex-col gap-4 p-4 sm:p-5">
              <div>
                <div className="mb-1.5 text-[12px] font-medium">Валюта заказа</div>
                <CurrencyToggle value={currency} onChange={cart.setCurrency} className="flex w-full" />
              </div>
              {(stores?.length ?? 0) > 0 && (
                <div>
                  <Label htmlFor="cart-store">Магазин</Label>
                  <Select id="cart-store" value={store} onChange={(event) => setStore(event.target.value)}>
                    <option value="">Без магазина (на себя)</option>
                    {(stores ?? []).map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.name}
                      </option>
                    ))}
                  </Select>
                </div>
              )}
              <fieldset>
                <legend className="mb-1.5 text-[12px] font-medium">Вид транспорта</legend>
                <div className="grid grid-cols-2 gap-2">
                  {TRANSPORTS.map(([value, label]) => (
                    <label
                      key={value}
                      className={cn(
                        "cursor-pointer rounded-lg border px-3 py-2 text-center text-sm font-medium transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[var(--ring)]/40",
                        transport === value
                          ? "border-[var(--primary)] bg-[var(--primary)]/5"
                          : "hover:bg-[var(--muted)]/40",
                      )}
                    >
                      <input
                        className="sr-only"
                        type="radio"
                        name="transport"
                        value={value}
                        checked={transport === value}
                        onChange={() => setTransport(value)}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <CartTotals priced={priced} currency={currency} />
              {error && (
                <p
                  role="alert"
                  className="rounded-md bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
                >
                  {error}
                </p>
              )}
              {catalog.error && <ErrorAlert message={catalog.error} onRetry={catalog.reload} />}
              <Button className="h-11" disabled={!canCheckout} onClick={checkout}>
                {busy ? "Оформляем…" : "Оформить заказ"}
              </Button>
              <p className="flex gap-2 text-xs text-[var(--muted-foreground)]">
                <Info className="mt-0.5 size-3.5 shrink-0" />
                Способ оплаты вы выберете после отгрузки.
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </AppShell>
  );
}

function CartRow({
  row,
  currency,
  disabled,
}: {
  row: PricedCart["rows"][number];
  currency: string;
  disabled: boolean;
}) {
  const cart = useCart();
  const { line, product } = row;
  if (!product) {
    return (
      <li className="flex items-center gap-3 px-4 py-3 sm:px-5">
        <ProductPhoto url={null} alt="" className="size-16 shrink-0 rounded-lg opacity-50" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-[var(--muted-foreground)]">Товар больше недоступен</div>
          <div className="text-xs text-[var(--muted-foreground)]">
            Он закончился или снят с продажи — в заказ не попадёт
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Удалить недоступный товар"
          disabled={disabled}
          onClick={() => cart.remove(line.product)}
        >
          <Trash2 className="size-4" />
        </Button>
      </li>
    );
  }
  return (
    <li className="grid grid-cols-[64px_minmax(0,1fr)_auto] gap-x-3 gap-y-2 px-4 py-3 sm:grid-cols-[72px_minmax(0,1fr)_140px_120px_auto] sm:items-center sm:px-5">
      <ProductPhoto
        url={product.photo_url}
        alt={product.label}
        className="size-16 rounded-lg sm:size-[72px]"
        iconClassName="size-5"
      />
      <div className="min-w-0">
        <div className="text-sm font-medium leading-snug">{product.label}</div>
        <div className="mt-0.5 text-xs text-[var(--muted-foreground)] tabular-nums">
          {product.price ? `${formatCurrency(product.price, currency)} × ${line.quantity}` : "Цена уточняется"}
        </div>
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="text-[var(--muted-foreground)] hover:text-[var(--destructive)] sm:order-last"
        aria-label={`Удалить из корзины: ${product.label}`}
        disabled={disabled}
        onClick={() => cart.remove(product.id)}
      >
        <Trash2 className="size-4" />
      </Button>
      <QuantityStepper
        value={line.quantity}
        onChange={(quantity) => cart.setQuantity(product.id, quantity)}
        label={product.label}
        className="col-span-2 col-start-2 sm:col-span-1 sm:col-start-auto"
      />
      <div className="col-start-2 text-sm font-semibold tabular-nums sm:col-start-auto sm:text-right">
        {row.total != null ? formatCurrency(row.total, currency) : "—"}
      </div>
    </li>
  );
}

function CartTotals({ priced, currency }: { priced: PricedCart; currency: string }) {
  return (
    <div className="flex flex-col gap-1.5 border-t pt-4 text-sm">
      <div className="flex justify-between">
        <span className="text-[var(--muted-foreground)]">Всего</span>
        <span className="font-medium tabular-nums">{bagsLabel(priced.quantity)}</span>
      </div>
      <div className="flex items-baseline justify-between">
        <span className="text-[var(--muted-foreground)]">Сумма</span>
        <span className="text-xl font-semibold tabular-nums">{formatCurrency(priced.total, currency)}</span>
      </div>
      {priced.hasUnpriced && (
        <p className="text-xs text-[var(--muted-foreground)]">
          У части товаров цена не закреплена — её подтвердит менеджер, сумма может вырасти.
        </p>
      )}
      {priced.hasUnavailable && (
        <p className="text-xs text-[var(--destructive)]">Недоступные товары не войдут в заказ.</p>
      )}
    </div>
  );
}

function EmptyCart() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-2 py-16 text-center">
        <span className="mb-1 grid size-14 place-items-center rounded-full bg-[var(--muted)] text-[var(--muted-foreground)]">
          <ShoppingCart className="size-6" />
        </span>
        <div className="font-medium">Корзина пуста</div>
        <p className="max-w-xs text-sm text-[var(--muted-foreground)]">
          Добавьте товары из каталога — они сохранятся здесь.
        </p>
        <Link href="/portal/catalog" className={buttonVariants({ className: "mt-3" })}>
          Перейти в каталог
        </Link>
      </CardContent>
    </Card>
  );
}

function OrderPlaced({ orderId }: { orderId: number }) {
  return (
    <Card className="mx-auto max-w-md">
      <CardContent className="flex flex-col items-center gap-2 py-12 text-center">
        <CheckCircle2 className="mb-1 size-12 text-[var(--success)]" />
        <div className="text-lg font-semibold">Заказ №{orderId} оформлен</div>
        <p className="max-w-xs text-sm text-[var(--muted-foreground)]">
          Менеджер проверит заявку и подтвердит цены. Статус — в «Мои заказы».
        </p>
        <div className="mt-4 flex flex-wrap justify-center gap-2">
          <Link href={`/portal/orders/${orderId}`} className={buttonVariants()}>
            Открыть заказ
          </Link>
          <Link href="/portal/catalog" className={buttonVariants({ variant: "outline" })}>
            Продолжить покупки
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
