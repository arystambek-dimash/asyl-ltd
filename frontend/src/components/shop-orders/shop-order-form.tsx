"use client";
import { useMemo, useRef, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  ClientPicker,
  SectionTitle,
  type OrderFormOptions,
  type OrderProductOption,
} from "@/components/orders/order-form-parts";
import { ProductPicker } from "@/components/orders/product-picker";
import { Button } from "@/components/ui/button";
import { DataGate, FormError } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import type { ShopOrder, ShopProduct } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { bagsLabel, formatCurrency } from "@/lib/utils";

type Line = { id: number; product: string; quantity: string };

export function ShopOrderForm({
  open,
  products,
  shopName,
  onClose,
  onCreated,
}: {
  open: boolean;
  products: ShopProduct[];
  shopName: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const clients = useApi<OrderFormOptions>(open ? "/orders/form-options/" : null);
  const [client, setClient] = useState("");
  const [search, setSearch] = useState("");
  const [clientListOpen, setClientListOpen] = useState(false);
  const [lines, setLines] = useState<Line[]>([{ id: 1, product: "", quantity: "" }]);
  const nextLine = useRef(2);
  const requestKey = useRef<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const options = useMemo<OrderProductOption[]>(
    () =>
      products.map((product) => ({
        ...product,
        available_bags: product.stock_bags,
        stock_by_warehouse: {},
        codes: [],
      })),
    [products],
  );

  function changed() {
    requestKey.current = null;
    setError("");
  }

  function updateLine(id: number, patch: Partial<Line>) {
    changed();
    setLines((current) => current.map((line) => (line.id === id ? { ...line, ...patch } : line)));
  }

  const valid =
    lines.length > 0 &&
    lines.every((line) => {
      const product = products.find((item) => String(item.id) === line.product);
      return product?.price != null && Number.isInteger(Number(line.quantity)) && Number(line.quantity) > 0;
    });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy || !valid) return;
    setBusy(true);
    setError("");
    requestKey.current ??= crypto.randomUUID();
    try {
      await api.post<ShopOrder>("/shop-orders/", {
        idempotency_key: requestKey.current,
        client: client ? Number(client) : null,
        items: lines.map((line) => ({ product: Number(line.product), quantity: Number(line.quantity) })),
      });
      requestKey.current = null;
      setLines([{ id: 1, product: "", quantity: "" }]);
      setClient("");
      setSearch("");
      setClientListOpen(false);
      onCreated();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !busy && onClose()}
      title="Новый заказ магазина"
      eyebrow={shopName}
      description="Продажа завершится сразу: товар спишется только с остатка магазина, без подтверждения грузчика."
      className="max-w-3xl"
      mobileFullscreen
      dismissible={false}
    >
      <form onSubmit={submit} className="space-y-5">
        <fieldset disabled={busy} className="space-y-5">
          <section className="space-y-3">
            <SectionTitle
              step={1}
              title="Покупатель"
              caption="Необязательно — можно оформить розничную продажу без клиента."
            />
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant={!client && !clientListOpen ? "default" : "outline"}
                size="sm"
                onClick={() => {
                  changed();
                  setClient("");
                  setClientListOpen(false);
                }}
              >
                Без клиента
              </Button>
              <Button
                type="button"
                variant={client || clientListOpen ? "default" : "outline"}
                size="sm"
                onClick={() => setClientListOpen(true)}
              >
                Выбрать клиента
              </Button>
            </div>
            {(client || clientListOpen) &&
              (clients.data ? (
                <ClientPicker
                  clients={clients.data.clients}
                  value={client}
                  search={search}
                  onSearch={setSearch}
                  listOpen={clientListOpen}
                  onOpenList={() => setClientListOpen(true)}
                  onChoose={(item) => {
                    changed();
                    setClient(String(item.id));
                    setClientListOpen(false);
                  }}
                />
              ) : (
                <DataGate loading={clients.loading} error={clients.error} onRetry={clients.reload} />
              ))}
          </section>
          <section className="space-y-3">
            <SectionTitle
              step={2}
              title="Товары"
              caption="Цена — из прайса магазина. Выберите товар и количество мешков."
            />
            {lines.map((line, index) => {
              const product = products.find((item) => String(item.id) === line.product);
              return (
                <div
                  key={line.id}
                  className="grid grid-cols-[1fr_2.75rem] gap-2 rounded-xl border bg-[var(--muted)]/30 p-3 sm:grid-cols-[minmax(0,1fr)_7rem_2.75rem]"
                >
                  <ProductPicker
                    products={options}
                    value={line.product}
                    onChange={(value) => updateLine(line.id, { product: value })}
                    bagsOf={(item) => item.available_bags ?? 0}
                    disabled={busy}
                    ariaLabel={`Товар, позиция ${index + 1}`}
                    className="col-span-full sm:col-span-1"
                  />
                  <Input
                    type="number"
                    min={1}
                    step={1}
                    required
                    value={line.quantity}
                    onChange={(event) => updateLine(line.id, { quantity: event.target.value })}
                    placeholder="Мешки"
                    aria-label={`Количество, позиция ${index + 1}`}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={`Убрать позицию ${index + 1}`}
                    disabled={busy || lines.length === 1}
                    onClick={() => {
                      changed();
                      setLines((current) => current.filter((item) => item.id !== line.id));
                    }}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                  {product && (
                    <p className="col-span-full text-xs text-[var(--muted-foreground)]">
                      В магазине: {bagsLabel(product.stock_bags)} ·{" "}
                      {product.price == null
                        ? "Нет цены в прайсе магазина"
                        : `${formatCurrency(product.price, product.currency)} за мешок`}
                    </p>
                  )}
                </div>
              );
            })}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                changed();
                setLines((current) => [...current, { id: nextLine.current++, product: "", quantity: "" }]);
              }}
            >
              <Plus className="size-4" /> Добавить товар
            </Button>
          </section>
        </fieldset>
        <FormError message={error} />
        <div className="flex flex-wrap justify-end gap-2 border-t pt-4">
          <Button type="button" variant="outline" disabled={busy} onClick={onClose}>
            Назад
          </Button>
          <Button type="submit" disabled={busy || !valid}>
            {busy ? "Сохраняем…" : "Завершить продажу"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
