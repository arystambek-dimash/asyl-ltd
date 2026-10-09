"use client";
import { useEffect, useRef, useState } from "react";
import { PackageCheck, Plus, Trash2 } from "lucide-react";
import {
  ClientPicker,
  EMPTY_FORM_OPTIONS,
  SectionTitle,
  type OrderClientOption,
  type OrderFormOptions,
  type OrderProductOption,
} from "@/components/orders/order-form-parts";
import { ProductPicker } from "@/components/orders/product-picker";
import { Button } from "@/components/ui/button";
import { DataGate, FormError } from "@/components/ui/data-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Select } from "@/components/ui/select";
import { api, apiError } from "@/lib/api";
import type { GoodsReturn } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { bagsLabel } from "@/lib/utils";

type Row = { id: number; product: string; bags: string };

/**
 * «Возврат»: клиент привёз мешки — менеджер выбирает клиента, любой товар
 * каталога и сколько мешков, возврат создаётся «Ждёт приёмки». Кладовщик
 * примет мешки, и принятые лягут на склад; долг и касса не меняются.
 */
export function GoodsReturnModal({
  open,
  onClose,
  onDone,
}: {
  open: boolean;
  onClose: () => void;
  onDone: () => void;
}) {
  const [created, setCreated] = useState<GoodsReturn | null>(null);

  // Созданный возврат уже в списке «Возвратов»: любое закрытие окна его перечитывает.
  function finish() {
    setCreated(null);
    onDone();
  }

  return (
    <Modal
      open={open}
      onClose={created ? finish : onClose}
      eyebrow="Работа · Возврат"
      title="Возврат товара"
      description={
        created
          ? undefined
          : "Клиент привёз мешки обратно — кладовщик примет их, и они лягут на склад. Долг и касса не меняются."
      }
      className={created ? "max-w-md" : "max-w-4xl"}
      mobileFullscreen
      dismissible={false}
    >
      {open &&
        (created ? (
          <GoodsReturnCreated created={created} onDone={finish} />
        ) : (
          <GoodsReturnForm onCancel={onClose} onCreated={setCreated} />
        ))}
    </Modal>
  );
}

/** Возврат создан и ждёт кладовщика: склад пополнится, когда он примет мешки. */
function GoodsReturnCreated({ created, onDone }: { created: GoodsReturn; onDone: () => void }) {
  return (
    <div role="status" className="flex flex-col items-center gap-4 py-4 text-center">
      <span className="flex size-14 items-center justify-center rounded-full bg-[var(--success)]/12 text-[var(--success)]">
        <PackageCheck className="size-7" />
      </span>
      <div className="flex flex-col gap-1">
        <div className="text-lg font-semibold">Возврат №{created.id} создан</div>
        <p className="text-sm text-[var(--muted-foreground)]">
          Ждёт приёмки у кладовщика — принятые мешки лягут на склад «{created.warehouse_name}».
        </p>
      </div>
      <Button className="w-full sm:w-auto" onClick={onDone}>
        Готово
      </Button>
    </div>
  );
}

/** Почему «Создать возврат» ещё недоступна; пусто — можно создавать. */
function missingText(client: string, rows: Row[]) {
  if (!client) return "Выберите клиента";
  const filled = rows.filter((row) => row.product || row.bags);
  if (filled.length === 0) return "Выберите товар и сколько мешков";
  if (filled.some((row) => !row.product)) return "Выберите товар в каждой строке";
  if (filled.some((row) => !(Number(row.bags) > 0))) return "Укажите мешки в каждой строке";
  return "";
}

function GoodsReturnForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: (created: GoodsReturn) => void }) {
  const { data, loading, error: loadError, reload } = useApi<OrderFormOptions>("/orders/form-options/");
  // Товары — весь действующий каталог: вернуть можно любой, не только купленный клиентом.
  const { clients, products, warehouses = [] } = data ?? EMPTY_FORM_OPTIONS;
  const [client, setClient] = useState("");
  const [search, setSearch] = useState("");
  const [listOpen, setListOpen] = useState(true);
  const [warehouse, setWarehouse] = useState("");
  const nextRowId = useRef(1);
  const [rows, setRows] = useState<Row[]>([{ id: 0, product: "", bags: "" }]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Второе нажатие, пока идёт запрос, не должно создать возврат дважды.
  const inFlight = useRef(false);

  useEffect(() => {
    if (warehouse || warehouses.length === 0) return;
    const initial = warehouses.find((item) => item.is_default) ?? warehouses[0];
    setWarehouse(String(initial.id));
  }, [warehouse, warehouses]);

  function chooseClient(item: OrderClientOption) {
    setClient(String(item.id));
    setSearch("");
    setListOpen(false);
    setError("");
  }

  function updateRow(id: number, patch: Partial<Row>) {
    setRows((current) => current.map((row) => (row.id === id ? { ...row, ...patch } : row)));
    setError("");
  }

  function removeRow(id: number) {
    setRows((current) =>
      current.length > 1
        ? current.filter((row) => row.id !== id)
        : [{ id: nextRowId.current++, product: "", bags: "" }],
    );
    setError("");
  }

  /** Остаток товара на выбранном складе — подсказка в списке, вернуть можно и товар без остатка. */
  const stockAt = (product: OrderProductOption) => product.stock_by_warehouse[warehouse] ?? 0;

  // Пустая строка не мешает; начатая — должна быть дописана.
  const lines = rows
    .filter((row) => row.product && Number(row.bags) > 0)
    .map((row) => ({ product: Number(row.product), bags: Number(row.bags) }));
  const missing = missingText(client, rows);
  const totalBags = lines.reduce((sum, line) => sum + line.bags, 0);

  async function submit() {
    if (inFlight.current || missing) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      const { data: row } = await api.post<GoodsReturn>(`/clients/${client}/goods-return/`, {
        warehouse: warehouse ? Number(warehouse) : null,
        lines,
      });
      onCreated(row);
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  if (!data) return <DataGate loading={loading} error={loadError || undefined} onRetry={reload} />;

  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_280px] lg:items-start">
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
          <SectionTitle step={2} title="Что вернули" caption="Любой товар и сколько мешков." />
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
            <div className="hidden grid-cols-[minmax(0,1fr)_112px_36px] gap-3 border-b border-slate-100 bg-slate-50/80 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500 sm:grid">
              <span>Товар</span>
              <span>Мешков</span>
              <span />
            </div>
            <div className="divide-y divide-slate-100">
              {rows.map((row, index) => (
                <div
                  key={row.id}
                  className="grid grid-cols-[minmax(0,1fr)_36px] gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_112px_36px] sm:items-center sm:gap-3"
                >
                  <ProductPicker
                    products={products}
                    value={row.product}
                    className="col-span-2 sm:col-span-1"
                    ariaLabel={`Товар ${index + 1}`}
                    bagsOf={stockAt}
                    allowOutOfStock
                    onChange={(product) => updateRow(row.id, { product })}
                  />
                  <Input
                    inputMode="numeric"
                    placeholder="Мешков"
                    aria-label={`Мешков ${index + 1}`}
                    value={row.bags}
                    onChange={(event) => updateRow(row.id, { bags: event.target.value.replace(/\D/g, "") })}
                    className="h-10 rounded-lg text-right tabular-nums"
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="justify-self-end text-slate-400 hover:text-red-600"
                    aria-label={`Убрать строку ${index + 1}`}
                    onClick={() => removeRow(row.id)}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 bg-slate-50/60 px-4 py-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="-ml-2"
                onClick={() => setRows((current) => [...current, { id: nextRowId.current++, product: "", bags: "" }])}
              >
                <Plus className="size-4" /> Ещё товар
              </Button>
              {totalBags > 0 && (
                <span className="text-xs text-slate-500">
                  Итого <b className="font-semibold tabular-nums text-slate-900">{bagsLabel(totalBags)}</b>
                </span>
              )}
            </div>
          </div>
        </section>

        <FormError message={error} />
      </div>

      <aside className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5">
        <div className="grid gap-1.5">
          <Label htmlFor="return-warehouse">Склад, где кладовщик примет мешки</Label>
          <Select
            id="return-warehouse"
            value={warehouse}
            onChange={(event) => {
              setWarehouse(event.target.value);
              setError("");
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
        <div className="flex flex-col gap-2 pt-2">
          <Button disabled={busy || Boolean(missing)} onClick={() => void submit()}>
            Создать возврат
          </Button>
          {missing && <p className="text-center text-xs text-slate-500">{missing}</p>}
          <Button variant="ghost" onClick={onCancel}>
            Отмена
          </Button>
        </div>
      </aside>
    </div>
  );
}
