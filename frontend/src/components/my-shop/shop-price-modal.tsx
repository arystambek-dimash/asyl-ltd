"use client";
import { useMemo, useState } from "react";
import {
  canViewProductColor,
  EMPTY_PRODUCT_DRAFT,
  ProductFields,
  productPayload,
  type ProductDraft,
} from "@/components/catalog/product-fields";
import { Button } from "@/components/ui/button";
import { FormError } from "@/components/ui/data-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import type { PortalProduct, Product } from "@/lib/types";
import { currencySymbol, formatCurrency } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const NEW_PRODUCT = "new";

/**
 * Товар в прайс своего магазина (право на цены): товар из каталога или новый
 * (право на каталог) и его цена. Пишет в общий прайс клиента-магазина — тот же,
 * что у бухгалтера в карточке клиента.
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
  const options = useMemo(() => [...catalog].sort((a, b) => a.label.localeCompare(b.label, "ru")), [catalog]);
  const [choice, setChoice] = useState(() => String(options[0]?.id ?? (canCreate ? NEW_PRODUCT : "")));
  const [draft, setDraft] = useState<ProductDraft>(EMPTY_PRODUCT_DRAFT);
  const [price, setPrice] = useState(() => priceInput(options[0]));
  // Товар, заведённый в этом окне: если цена не сохранилась, повтор не создаст дубль.
  const [created, setCreated] = useState<Product | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const isNew = choice === NEW_PRODUCT;

  function choose(value: string) {
    setChoice(value);
    setPrice(priceInput(options.find((product) => String(product.id) === value)));
    setError("");
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      let product: { id: number; label: string } | undefined = isNew
        ? (created ?? undefined)
        : options.find((item) => String(item.id) === choice);
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
          <Button type="submit" form="shop-price-form" disabled={busy || !choice || !(Number(price) > 0)}>
            {busy ? "Сохраняем…" : "Сохранить цену"}
          </Button>
        </>
      }
    >
      <form id="shop-price-form" onSubmit={save} className="flex flex-col gap-4">
        <FormError message={error} />
        <Field label="Товар" htmlFor="shop-price-product">
          <Select
            id="shop-price-product"
            value={choice}
            disabled={busy || Boolean(created)}
            onChange={(event) => choose(event.target.value)}
          >
            {options.map((product) => (
              <option key={product.id} value={product.id}>
                {product.label}
                {product.price ? ` · ${formatCurrency(product.price, currency)}` : " · без цены"}
              </option>
            ))}
            {canCreate && <option value={NEW_PRODUCT}>+ Новый товар…</option>}
          </Select>
        </Field>
        {isNew && !created && (
          <ProductFields idPrefix="shop-product" draft={draft} onChange={setDraft} canViewColor={canViewColor} />
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
              autoFocus={!isNew}
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

function priceInput(product: PortalProduct | undefined): string {
  return product?.price ? String(Number(product.price)) : "";
}
