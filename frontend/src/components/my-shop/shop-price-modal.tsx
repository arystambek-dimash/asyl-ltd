"use client";
import { useState } from "react";
import { ArrowLeft, Plus } from "lucide-react";
import {
  canViewProductColor,
  EMPTY_PRODUCT_DRAFT,
  ProductFields,
  productPayload,
  type ProductDraft,
} from "@/components/catalog/product-fields";
import { ProductPicker } from "@/components/orders/product-picker";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import type { PortalProduct, Product } from "@/lib/types";
import { currencySymbol, formatCurrency } from "@/lib/utils";
import { useAuth } from "@/store/auth";

/**
 * Товар в прайс своего магазина (право на цены): товар из каталога — тем же
 * выбором, что в заказе, — или новый (право на каталог) и его цена. Пишет в
 * общий прайс клиента-магазина — тот же, что у бухгалтера в карточке клиента.
 */
export function ShopPriceModal({
  clientId,
  currency,
  catalog,
  onClose,
  onSaved,
}: {
  clientId: number;
  currency: "KZT" | "USD";
  catalog: PortalProduct[];
  onClose: () => void;
  onSaved: (label: string) => void;
}) {
  const { me } = useAuth();
  const canCreate = can(me, "catalog.create");
  const canViewColor = canViewProductColor(me);
  const [choice, setChoice] = useState("");
  const [isNew, setIsNew] = useState(() => catalog.length === 0 && canCreate);
  const [draft, setDraft] = useState<ProductDraft>(EMPTY_PRODUCT_DRAFT);
  const [price, setPrice] = useState("");
  // Товар, заведённый в этом окне: если цена не сохранилась, повтор не создаст дубль.
  const [created, setCreated] = useState<Product | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function choose(productId: string) {
    setChoice(productId);
    const product = catalog.find((item) => String(item.id) === productId);
    setPrice(product?.price ? String(Number(product.price)) : "");
    setError("");
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      let product: { id: number; label: string } | undefined = isNew
        ? (created ?? undefined)
        : catalog.find((item) => String(item.id) === choice);
      if (!product) {
        const response = await api.post<Product>("/products/", productPayload(draft, { canViewColor, editing: false }));
        setCreated(response.data);
        product = response.data;
      }
      await api.put(`/clients/${clientId}/prices/`, {
        prices: [{ product: product.id, currency, price: price.trim() }],
      });
      onSaved(product.label);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Товар в прайс магазина"
      description={`Цена за мешок для заказов «Моего магазина», в ${currencySymbol(currency)}.`}
      footer={
        <>
          <Button variant="ghost" disabled={busy} onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" form="shop-price-form" disabled={busy || (!isNew && !choice) || !(Number(price) > 0)}>
            {busy ? "Сохраняем…" : "Сохранить цену"}
          </Button>
        </>
      }
    >
      <form id="shop-price-form" onSubmit={save} className="flex flex-col gap-4">
        <FormError message={error} />
        {isNew ? (
          <>
            {created ? (
              <p className="text-sm">Товар «{created.label}» заведён — осталось сохранить цену.</p>
            ) : (
              <ProductFields
                idPrefix="shop-product"
                draft={draft}
                onChange={setDraft}
                canViewColor={canViewColor}
                autoFocus
              />
            )}
            {catalog.length > 0 && !created && (
              <Button type="button" variant="ghost" size="sm" className="self-start" onClick={() => setIsNew(false)}>
                <ArrowLeft className="size-4" /> Выбрать из каталога
              </Button>
            )}
          </>
        ) : (
          <>
            <Field label="Товар">
              <div className="flex flex-col gap-2">
                <ProductPicker
                  products={catalog}
                  value={choice}
                  onChange={choose}
                  ariaLabel="Товар"
                  disabled={busy}
                  hintOf={(product) =>
                    product.price
                      ? { text: formatCurrency(product.price, currency) }
                      : { text: "без цены", muted: true }
                  }
                />
              </div>
            </Field>
            {canCreate && (
              <Button type="button" variant="ghost" size="sm" className="self-start" onClick={() => setIsNew(true)}>
                <Plus className="size-4" /> Новый товар
              </Button>
            )}
          </>
        )}
        <Field label="Цена за мешок" htmlFor="shop-price-value">
          <div className="relative">
            <Input
              id="shop-price-value"
              type="number"
              min="0.01"
              step="0.01"
              inputMode="decimal"
              className="pr-9 text-right tabular-nums"
              value={price}
              onChange={(event) => setPrice(event.target.value)}
              required
            />
            <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm font-medium text-[var(--muted-foreground)]">
              {currencySymbol(currency)}
            </span>
          </div>
        </Field>
      </form>
    </Modal>
  );
}
