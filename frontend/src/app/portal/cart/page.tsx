"use client";
import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, Info, ShoppingCart, Trash2 } from "lucide-react";
import { AppShell } from "@/components/layout/app-shell";
import { ProductPhoto } from "@/components/catalog/product-photo";
import { CurrencyToggle } from "@/components/portal/currency-toggle";
import { QuantityStepper } from "@/components/portal/quantity-stepper";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { DataGate, ErrorAlert, FormError } from "@/components/ui/data-state";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { priceCart, type PricedCart } from "@/lib/cart";
import type { PortalOrder, Store } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { usePortalCatalog } from "@/lib/use-portal-catalog";
import { bagsLabel, cn, formatCurrency, pluralRu } from "@/lib/utils";
import { useCart } from "@/store/cart";

type Transport = PortalOrder["transport_type"];

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
              <div className="truncate font-semibold tabular-nums">{totalLabel(priced, currency)}</div>
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
              <FormError message={error} />
              {catalog.error && <ErrorAlert message={catalog.error} onRetry={catalog.reload} />}
              {/* На телефоне кнопка — в нижней панели, вторая в карточке только дублировала бы её. */}
              <Button className="hidden h-11 md:inline-flex" disabled={!canCheckout} onClick={checkout}>
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
  const removeButton = (className: string) => (
    <Button
      variant="ghost"
      size="icon"
      className={cn("shrink-0 text-[var(--muted-foreground)] hover:text-[var(--destructive)]", className)}
      aria-label={`Удалить из корзины: ${product.label}`}
      disabled={disabled}
      onClick={() => cart.remove(product.id)}
    >
      <Trash2 className="size-4" />
    </Button>
  );
  return (
    <li className="flex gap-3 px-4 py-4 sm:items-center sm:px-5">
      <ProductPhoto
        url={product.photo_url}
        alt={product.label}
        className="size-14 shrink-0 rounded-lg sm:size-16"
        iconClassName="size-5"
      />
      <div className="flex min-w-0 flex-1 flex-col gap-3 sm:flex-row sm:items-center sm:gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-2">
            <div className="text-sm font-medium leading-snug">{product.label}</div>
            {removeButton("-mr-2 -mt-1.5 size-8 sm:hidden")}
          </div>
          <div className="mt-0.5 text-xs text-[var(--muted-foreground)] tabular-nums">
            {product.price ? `${formatCurrency(product.price, currency)} × ${line.quantity}` : "Цена уточняется"}
          </div>
        </div>
        {/* На телефоне количество и сумма — одной строкой под названием, на десктопе — колонками. */}
        <div className="flex items-center justify-between gap-3 sm:contents">
          <QuantityStepper
            value={line.quantity}
            onChange={(quantity) => cart.setQuantity(product.id, quantity)}
            label={product.label}
            className="w-36 sm:w-40"
          />
          <div className="text-right sm:w-32">
            {row.total != null ? (
              <span className="text-sm font-semibold tabular-nums">{formatCurrency(row.total, currency)}</span>
            ) : (
              <span className="whitespace-nowrap text-xs text-[var(--muted-foreground)]">уточнит менеджер</span>
            )}
          </div>
        </div>
        {removeButton("hidden sm:inline-flex")}
      </div>
    </li>
  );
}

/** Сумма корзины: если ни у одного товара нет цены, «0 ₸» вводил бы в заблуждение. */
function totalLabel(priced: PricedCart, currency: string) {
  return priced.total === 0 && priced.hasUnpriced ? "Уточнит менеджер" : formatCurrency(priced.total, currency);
}

function CartTotals({ priced, currency }: { priced: PricedCart; currency: string }) {
  const pricedPart = priced.total > 0;
  return (
    <div className="flex flex-col gap-1.5 border-t pt-4 text-sm">
      <div className="flex justify-between">
        <span className="text-[var(--muted-foreground)]">Всего</span>
        <span className="font-medium tabular-nums">{bagsLabel(priced.quantity)}</span>
      </div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[var(--muted-foreground)]">Сумма</span>
        <span
          className={cn("tabular-nums", pricedPart || !priced.hasUnpriced ? "text-xl font-semibold" : "font-medium")}
        >
          {totalLabel(priced, currency)}
        </span>
      </div>
      {priced.hasUnpriced && (
        <p className="text-xs text-[var(--muted-foreground)]">
          {pricedPart
            ? "Без товаров, цену которых подтвердит менеджер: итог может вырасти."
            : "Цена на эти товары ещё не закреплена — менеджер подтвердит её после заявки."}
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
