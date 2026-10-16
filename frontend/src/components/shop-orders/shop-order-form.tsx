"use client";
import { useMemo, useRef, useState } from "react";
import { Check, Plus } from "lucide-react";
import { OrderComposition, OrderCompositionRow } from "@/components/orders/order-composition";
import {
  ClientPicker,
  SectionTitle,
  type OrderFormOptions,
  type OrderProductOption,
} from "@/components/orders/order-form-parts";
import { Button } from "@/components/ui/button";
import { DataGate, FormError } from "@/components/ui/data-state";
import { Modal } from "@/components/ui/modal";
import { api, apiError } from "@/lib/api";
import { moneyCents } from "@/lib/debt-orders";
import { formatEstimate, requestEstimate } from "@/lib/orders";
import type { OrderDraftRow } from "@/lib/order-draft";
import type { ShopOrder, ShopProduct, ShopSummary } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { formatCount } from "@/lib/utils";

export function ShopOrderForm({
  open,
  products,
  shopName,
  currency,
  onClose,
  onCreated,
}: {
  open: boolean;
  products: ShopProduct[];
  shopName: string;
  currency: ShopSummary["currency"];
  onClose: () => void;
  onCreated: () => void;
}) {
  const clients = useApi<OrderFormOptions>(open ? "/orders/form-options/" : null);
  const [client, setClient] = useState("");
  const [search, setSearch] = useState("");
  const [clientListOpen, setClientListOpen] = useState(false);
  const [lines, setLines] = useState<OrderDraftRow[]>([{ id: 1, product: "", quantity: "", price: "" }]);
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
  const selectedClient = clients.data?.clients.find((item) => String(item.id) === client);
  const validLines = lines.filter((line) => line.product && Number(line.quantity) > 0);
  const estimate = requestEstimate(validLines.map((line) => ({ quantity: line.quantity, unit_price: line.price })));
  const totalLabel = formatEstimate(estimate.amount, currency);
  const bagsText = `${estimate.bags} меш.`;

  function changed() {
    requestKey.current = null;
    setError("");
  }

  function updateLine(id: number, patch: Partial<OrderDraftRow>) {
    changed();
    setLines((current) => current.map((line) => (line.id === id ? { ...line, ...patch } : line)));
  }

  const valid =
    lines.length > 0 &&
    lines.every(
      (line) =>
        products.some((item) => String(item.id) === line.product) &&
        Number.isInteger(Number(line.quantity)) &&
        Number(line.quantity) > 0 &&
        line.price.trim() !== "" &&
        Number.isFinite(Number(line.price)) &&
        Number(line.price) > 0,
    );

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
        items: lines.map((line) => ({
          product: Number(line.product),
          quantity: Number(line.quantity),
          unit_price: (moneyCents(line.price) / 100).toFixed(2),
        })),
      });
      requestKey.current = null;
      setLines([{ id: 1, product: "", quantity: "", price: "" }]);
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
      title="Новый заказ"
      eyebrow={`Работа · ${shopName}`}
      description="Продажа из магазина. Проверьте клиента, состав и цены перед созданием заказа."
      className="max-w-5xl"
      mobileFullscreen
      dismissible={false}
    >
      <form onSubmit={submit} className="flex flex-col gap-5">
        <fieldset disabled={busy} className="grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
          <div className="min-w-0 space-y-8">
            <section className="space-y-4">
              <SectionTitle
                step={1}
                title="Клиент"
                caption="Кому оформляем заказ. Розничную продажу можно оформить без клиента."
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
            <section className="space-y-4">
              <SectionTitle
                step={2}
                title="Состав заказа"
                caption={`Цены подставляются из прайса магазина в ${currency}. Их можно изменить или ввести вручную.`}
              />
              <OrderComposition
                currency={currency}
                footer={
                  <>
                    <button
                      type="button"
                      onClick={() => {
                        changed();
                        setLines((current) => [
                          ...current,
                          { id: nextLine.current++, product: "", quantity: "", price: "" },
                        ]);
                      }}
                      className="inline-flex items-center gap-1.5 text-sm font-semibold text-slate-700 transition hover:text-slate-900 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <Plus className="size-4" /> Добавить позицию
                    </button>
                    <span className="text-xs text-slate-500">
                      {bagsText} · <b className="font-semibold tabular-nums text-slate-900">{totalLabel}</b>
                    </span>
                  </>
                }
              >
                {lines.map((line, index) => (
                  <OrderCompositionRow
                    key={line.id}
                    row={line}
                    index={index}
                    products={options}
                    currency={currency}
                    disabled={busy}
                    hintOf={(product) => ({
                      text: `${formatCount(product.available_bags ?? 0)} меш.`,
                      muted: (product.available_bags ?? 0) <= 0,
                    })}
                    onProduct={(product) =>
                      updateLine(line.id, {
                        product,
                        price: products.find((item) => String(item.id) === product)?.price ?? "",
                      })
                    }
                    onQuantity={(quantity) => updateLine(line.id, { quantity })}
                    onPrice={(price) => updateLine(line.id, { price })}
                    onRemove={() => {
                      changed();
                      setLines((current) =>
                        current.length > 1
                          ? current.filter((item) => item.id !== line.id)
                          : [{ id: nextLine.current++, product: "", quantity: "", price: "" }],
                      );
                    }}
                  />
                ))}
              </OrderComposition>
            </section>
          </div>
          <aside className="space-y-4 lg:sticky lg:top-0">
            <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">Итог</div>
              <div className="mt-1 text-2xl font-bold tabular-nums text-slate-900">{totalLabel}</div>
              <div className="text-xs text-slate-500">
                {validLines.length} {validLines.length === 1 ? "позиция" : "позиций"} · {bagsText}
              </div>
              <dl className="mt-3 space-y-1.5 border-t border-slate-200 pt-3 text-xs">
                {[
                  { label: "Клиент", value: selectedClient?.name ?? "Розничный покупатель" },
                  { label: "Магазин", value: shopName },
                ].map((row) => (
                  <div key={row.label} className="flex items-baseline justify-between gap-3">
                    <dt className="shrink-0 text-slate-500">{row.label}</dt>
                    <dd className="truncate text-right font-medium text-slate-800">{row.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </aside>
        </fieldset>
        <FormError message={error} className="rounded-lg py-2.5 font-medium" />
        <div className="sticky -bottom-5 z-10 flex items-center justify-end gap-2 border-t border-slate-200 bg-white/95 pb-1 pt-3 backdrop-blur-md">
          <span className="mr-auto text-sm text-slate-500 lg:hidden">
            <b className="font-semibold tabular-nums text-slate-900">{totalLabel}</b> · {bagsText}
          </span>
          <Button type="button" variant="ghost" onClick={onClose} disabled={busy}>
            Отмена
          </Button>
          <Button type="submit" disabled={busy || !valid}>
            {busy ? "Сохранение…" : "Создать заказ"}
            {!busy && <Check className="size-4" />}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
