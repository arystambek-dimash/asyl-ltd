"use client";
import { useEffect, useRef, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import {
  ClientPicker,
  EMPTY_FORM_OPTIONS,
  SectionTitle,
  type OrderClientOption,
  type OrderFormOptions,
} from "@/components/orders/order-form-parts";
import { Button } from "@/components/ui/button";
import { DataGate, FormError } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Segmented } from "@/components/ui/segmented";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { showSuccess } from "@/lib/toast";
import { useApi } from "@/lib/use-api";
import { bagsLabel, formatCurrency, formatIsoDayMonth } from "@/lib/utils";
import { useAuth } from "@/store/auth";

type Settlement = "debt" | "cash";
type Row = { id: number; product: string; bags: string };

/** Раскладка возврата с сервера: заказ → мешки и сумма в валюте заказа (orders/goods_returns.py). */
interface GoodsReturnPlan {
  settlement: Settlement;
  bags: number;
  /** Итог по валютам: разные валюты не складываются. */
  amounts: Record<string, string>;
  orders: {
    order_id: number;
    currency: string;
    shipped_at: string;
    amount: string;
    lines: { label: string; bags: number; amount: string }[];
  }[];
  return_id?: number;
}

/** Мука, которую клиент может вернуть (GET /clients/{id}/goods-return/): сколько поместится в каждом режиме. */
interface ReturnableProduct {
  product: number;
  label: string;
  debt_bags: number;
  cash_bags: number;
}

/** «Возврат»: клиент привёз мешки — сервер раскладывает их по его отгруженным заказам. */
export function GoodsReturnModal({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Работа · Возврат"
      title="Возврат товара"
      description="Клиент привёз мешки обратно — система сама разложит их по его отгруженным заказам."
      className="max-w-5xl"
      mobileFullscreen
      dismissible={false}
    >
      {open && <GoodsReturnForm onCancel={onClose} onDone={onDone} />}
    </Modal>
  );
}

/** Сколько мешков этой муки поместится в выбранном режиме — подсказка под строкой. */
function ReturnLimit({ product, settlement }: { product?: ReturnableProduct; settlement: Settlement }) {
  if (!product) return null;
  const bags = settlement === "debt" ? product.debt_bags : product.cash_bags;
  return (
    <p className={bags ? "text-xs text-slate-500" : "text-xs font-medium text-[var(--destructive)]"}>
      {bags
        ? `Можно вернуть до ${bagsLabel(bags)} ${settlement === "debt" ? "в счёт долга" : "из кассы"}`
        : settlement === "debt"
          ? "Нет заказов в долге с этой мукой — выберите «Деньги из кассы»"
          : "Нет оплаченных заказов с этой мукой — выберите «Уменьшить долг»"}
    </p>
  );
}

function GoodsReturnForm({ onCancel, onDone }: { onCancel: () => void; onDone: () => void }) {
  const { me } = useAuth();
  const { data, loading, error: loadError, reload } = useApi<OrderFormOptions>("/orders/form-options/");
  const { clients, warehouses = [] } = data ?? EMPTY_FORM_OPTIONS;
  const [client, setClient] = useState("");
  const [search, setSearch] = useState("");
  const [listOpen, setListOpen] = useState(true);
  const [warehouse, setWarehouse] = useState("");
  const [settlement, setSettlement] = useState<Settlement>("debt");
  const nextRowId = useRef(1);
  const [rows, setRows] = useState<Row[]>([{ id: 0, product: "", bags: "" }]);
  const [plan, setPlan] = useState<GoodsReturnPlan | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Второе нажатие, пока идёт запрос, не должно провести возврат дважды.
  const inFlight = useRef(false);
  const canCash = can(me, "payments.confirm");
  const returnable = useApi<{ products: ReturnableProduct[] }>(client ? `/clients/${client}/goods-return/` : null);
  const products = returnable.data?.products ?? [];

  useEffect(() => {
    if (warehouse || warehouses.length === 0) return;
    const initial = warehouses.find((item) => item.is_default) ?? warehouses[0];
    setWarehouse(String(initial.id));
  }, [warehouse, warehouses]);

  // Любая правка делает раскладку устаревшей: подтверждать можно только свежую.
  function edited() {
    setPlan(null);
    setError("");
  }

  function chooseClient(item: OrderClientOption) {
    setClient(String(item.id));
    setSearch("");
    setListOpen(false);
    edited();
  }

  function updateRow(id: number, patch: Partial<Row>) {
    setRows((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)));
    edited();
  }

  // Пустая строка не мешает; начатая — должна быть дописана.
  const filled = rows.filter((row) => row.product || row.bags);
  const lines = filled
    .filter((row) => row.product && Number(row.bags) > 0)
    .map((row) => ({ product: Number(row.product), bags: Number(row.bags) }));
  const ready = Boolean(client) && lines.length > 0 && lines.length === filled.length;

  async function submit(preview: boolean) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const { data: result } = await api.post<GoodsReturnPlan>(`/clients/${client}/goods-return/`, {
        settlement,
        warehouse: warehouse ? Number(warehouse) : null,
        lines,
        preview,
      });
      if (preview) {
        setPlan(result);
        return;
      }
      showSuccess(`Возврат №${result.return_id}: ${bagsLabel(result.bags)}`);
      onDone();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (!data) return <DataGate loading={loading} error={loadError || undefined} onRetry={reload} />;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
      <div className="min-w-0 space-y-8">
        <section className="space-y-4">
          <SectionTitle step={1} title="Клиент" caption="Кто привёз мешки." />
          <ClientPicker
            clients={clients}
            value={client}
            search={search}
            onSearch={setSearch}
            listOpen={listOpen || !client}
            onOpenList={() => setListOpen(true)}
            onChoose={chooseClient}
          />
        </section>

        <section className="space-y-4">
          <SectionTitle step={2} title="Что вернули" caption="Мука и сколько мешков — цену берём из заказов." />
          {client && returnable.data && products.length === 0 && (
            <p className="rounded-lg bg-slate-50 px-3 py-2 text-sm text-slate-600">
              У клиента нет отгруженной муки, которую можно вернуть.
            </p>
          )}
          <FormError message={returnable.error} />
          {rows.map((row, index) => (
            <div key={row.id} className="space-y-1">
              <div className="grid grid-cols-[minmax(0,1fr)_110px_auto] items-end gap-2">
                <div className="grid min-w-0 gap-1.5">
                  <Label htmlFor={`return-product-${row.id}`}>Мука {index + 1}</Label>
                  <Select
                    id={`return-product-${row.id}`}
                    value={row.product}
                    disabled={!client}
                    onChange={(event) => updateRow(row.id, { product: event.target.value })}
                    className="h-10 rounded-lg bg-white"
                  >
                    <option value="">{client ? "Выберите муку" : "Сначала выберите клиента"}</option>
                    {products.map((product) => (
                      <option key={product.product} value={product.product}>
                        {product.label}
                      </option>
                    ))}
                  </Select>
                </div>
                <div className="grid gap-1.5">
                  <Label htmlFor={`return-bags-${row.id}`}>Мешков {index + 1}</Label>
                  <Input
                    id={`return-bags-${row.id}`}
                    inputMode="numeric"
                    value={row.bags}
                    onChange={(event) => updateRow(row.id, { bags: event.target.value.replace(/\D/g, "") })}
                    className="h-10 text-right tabular-nums"
                  />
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-10"
                  aria-label={`Убрать строку ${index + 1}`}
                  disabled={rows.length === 1}
                  onClick={() => {
                    setRows((current) => current.filter((item) => item.id !== row.id));
                    edited();
                  }}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
              <ReturnLimit
                product={products.find((item) => String(item.product) === row.product)}
                settlement={settlement}
              />
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              setRows((current) => [...current, { id: nextRowId.current++, product: "", bags: "" }]);
              edited();
            }}
          >
            <Plus className="size-4" /> Ещё мука
          </Button>
        </section>

        {plan && (
          <section
            aria-label="Раскладка возврата"
            className="space-y-3 rounded-xl border border-slate-200 bg-slate-50/70 p-4"
          >
            {plan.orders.map((order) => (
              <div key={order.order_id} className="flex items-start justify-between gap-3 text-sm">
                <div className="min-w-0">
                  <div className="font-semibold">
                    #{order.order_id} · отгружен {formatIsoDayMonth(order.shipped_at.slice(0, 10))}
                  </div>
                  {order.lines.map((line, index) => (
                    <div key={index} className="text-slate-600">
                      {line.label} — {bagsLabel(line.bags)}
                    </div>
                  ))}
                </div>
                <div className="shrink-0 font-semibold tabular-nums">
                  {formatCurrency(order.amount, order.currency)}
                </div>
              </div>
            ))}
            <div className="flex flex-wrap justify-between gap-2 border-t border-slate-200 pt-3 font-bold">
              <span>Итого {bagsLabel(plan.bags)}</span>
              {/* Валюта — у каждого заказа своя: суммы разных валют не складываются. */}
              <span className="tabular-nums">
                {plan.settlement === "cash" ? "Касса отдаёт: " : "Долг уменьшится на "}
                {Object.entries(plan.amounts)
                  .map(([currency, amount]) => formatCurrency(amount, currency))
                  .join(" и ")}
              </span>
            </div>
          </section>
        )}

        <FormError message={error} />
      </div>

      <aside className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="grid gap-1.5">
          <Label htmlFor="return-warehouse">Склад, куда кладём мешки</Label>
          <Select
            id="return-warehouse"
            value={warehouse}
            onChange={(event) => {
              setWarehouse(event.target.value);
              edited();
            }}
            className="h-10 rounded-lg bg-white"
          >
            {warehouses.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
                {item.is_default ? " · основной" : ""}
              </option>
            ))}
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label>Деньги</Label>
          <Segmented
            ariaLabel="Деньги"
            value={settlement}
            options={[
              { value: "debt", label: "Уменьшить долг" },
              { value: "cash", label: "Деньги из кассы", disabled: !canCash },
            ]}
            onChange={(value) => {
              setSettlement(value);
              edited();
            }}
          />
        </div>
        <div className="flex flex-col gap-2 pt-2">
          {plan ? (
            <Button disabled={busy} onClick={() => void submit(false)}>
              Подтвердить возврат
            </Button>
          ) : (
            <Button disabled={busy || !ready} onClick={() => void submit(true)}>
              Проверить
            </Button>
          )}
          <Button variant="ghost" onClick={onCancel}>
            Отмена
          </Button>
        </div>
      </aside>
    </div>
  );
}
