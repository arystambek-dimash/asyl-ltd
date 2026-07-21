"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { LicensePlateInput } from "@/components/ui/license-plate-input";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";

import { Plus, Trash2, Info } from "lucide-react";
import type { Client, Department, Order, Product, Store } from "@/lib/types";

type Row = { product: string; quantity: string; price: string };

/**
 * Форма заказа: создание и редактирование.
 * При создании отдел продаж выбирается явно и фильтрует список клиентов —
 * заказ наследует отдел клиента. При редактировании клиент зафиксирован,
 * позиции можно менять до начала загрузки.
 */
export function OrderForm({ editing, onCancel, onDone }: {
  editing?: Order | null;
  onCancel: () => void;
  onDone: () => void;
}) {
  const router = useRouter();
  const { data: clients } = useApi<Client[]>("/clients/");
  const { data: products } = useApi<Product[]>("/products/");
  const { data: stores } = useApi<Store[]>("/stores/");
  const { data: departments } = useApi<Department[]>("/departments/");
  const [dept, setDept] = useState(editing?.department ?? "");
  const [client, setClient] = useState(editing ? String(editing.client) : "");
  const [currency, setCurrency] = useState<"KZT" | "USD">(editing?.currency ?? "KZT");
  const [store, setStore] = useState(editing?.store ? String(editing.store) : "");
  const [transport, setTransport] = useState<"truck" | "train">(editing?.transport_type ?? "truck");
  const [truck, setTruck] = useState(editing?.truck_number ?? "");
  const [arrival, setArrival] = useState(editing?.arrival_date ?? "");
  const [rows, setRows] = useState<Row[]>(editing
    ? editing.items.map((it) => ({
        product: String(it.product),
        quantity: String(it.quantity),
        price: it.unit_price ?? it.price ?? "",
      }))
    : [{ product: "", quantity: "", price: "" }]);
  const [clientPrices, setClientPrices] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (dept || !departments?.length) return;
    setDept((departments.find((department) => department.is_default) ?? departments[0]).code);
  }, [dept, departments]);

  // При выборе клиента подтягиваем его цены и предзаполняем строки.
  useEffect(() => {
    if (!client) { setClientPrices({}); return; }
    // Быстрое переключение клиентов: медленный старый ответ не должен
    // перезаписать цены уже выбранного клиента.
    let stale = false;
    api.get<Record<string, string>>(`/client-prices/?client=${client}&currency=${currency}`)
      .then((r) => {
        if (stale) return;
        setClientPrices(r.data);
        if (!editing) {
          setRows((rs) => rs.map((row) => row.product
            ? { ...row, price: r.data[row.product] ?? "" }
            : row));
        }
      })
      .catch(() => { if (!stale) setClientPrices({}); });
    return () => { stale = true; };
  }, [client, currency, editing]);

  const total = rows.reduce((s, r) => s + Number(r.price || 0) * Number(r.quantity || 0), 0);
  const selectedCurrency = currency;
  const allPriced = rows.filter((r) => r.product && Number(r.quantity) > 0)
    .every((r) => Number(r.price) > 0);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const valid = rows.filter((r) => r.product && Number(r.quantity) > 0);
      if (!valid.length) throw new Error("empty");
      const items = valid.map((r) => ({ product: Number(r.product), quantity: Number(r.quantity) }));
      const prices = Object.fromEntries(valid.map((r) => [r.product, r.price]));
      const body = {
        department: dept,
        store: store ? Number(store) : null,
        transport_type: transport,
        truck_number: transport === "train" ? "" : truck,
        arrival_date: arrival || null,
        currency,
        items,
        prices,
      };
      if (editing) {
        await api.patch(`/orders/${editing.id}/`, body);
        onDone();
      } else {
        const { data } = await api.post("/orders/", { ...body, client: Number(client) });
        onDone();
        router.push(`/orders/${data.id}`);
      }
    } catch (err) {
      setError(err instanceof Error && err.message === "empty"
        ? "Добавьте хотя бы одну позицию." : apiError(err));
    } finally { setBusy(false); }
  }

  const clientStores = (stores ?? []).filter((s) => String(s.client) === client);

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <div className="grid gap-2.5">
        <div className="flex items-center justify-between gap-3">
          <Label>Отдел продаж</Label>
          <span className="text-[11px] text-[var(--muted-foreground)]">Учитывается в аналитике заказа</span>
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {(departments ?? []).map((department) => (
            <button key={department.code} type="button"
              onClick={() => setDept(department.code)}
              className={
                "group flex min-h-14 items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left text-sm font-semibold transition-all " +
                (dept === department.code
                  ? "border-transparent bg-[var(--foreground)] text-[var(--background)] shadow-md"
                  : "bg-[var(--card)] hover:-translate-y-0.5 hover:shadow-sm")
              }>
              <span className="size-2.5 shrink-0 rounded-full ring-4 ring-current/10"
                style={{ backgroundColor: department.color, color: department.color }} />
              <span className="truncate">{department.name}</span>
            </button>
          ))}
          {(departments ?? []).length === 0 && (
            <div className="col-span-full rounded-xl border border-dashed px-4 py-4 text-sm text-[var(--muted-foreground)]">
              Нет активных отделов. Администратор может добавить их на странице заказов.
            </div>
          )}
          </div>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label>Клиент</Label>
          <Select value={client} disabled={!!editing}
            onChange={(e) => {
              const value = e.target.value;
              setClient(value); setStore("");
              const selected = clients?.find((item) => String(item.id) === value);
              if (selected) setCurrency(selected.currency);
            }} required>
            <option value="">Выберите клиента</option>
            {(clients ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
          {editing && (
            <span className="text-xs text-[var(--muted-foreground)]">
              Клиент фиксируется при создании заказа.
            </span>
          )}
        </div>
        <div className="grid gap-2">
          <Label>Магазин (необязательно)</Label>
          <Select value={store} onChange={(e) => setStore(e.target.value)}
            disabled={!client || clientStores.length === 0}>
            <option value="">Без магазина (на клиента)</option>
            {clientStores.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>
          {client && clientStores.length === 0 && (
            <span className="text-xs text-[var(--muted-foreground)]">
              У клиента нет магазинов — добавьте в разделе «Магазины».
            </span>
          )}
        </div>
      </div>

      <div className="grid gap-2">
        <div className="flex items-center justify-between gap-3">
          <Label>Валюта заказа</Label>
          <span className="text-[11px] text-[var(--muted-foreground)]">
            Оплата будет принята в этой же валюте
          </span>
        </div>
        <div className="grid grid-cols-2 gap-2 rounded-xl bg-[var(--muted)]/35 p-1.5">
          {([[
            "KZT", "₸", "Тенге",
          ], ["USD", "$", "Доллары"]] as const).map(([code, symbol, label]) => (
            <button key={code} type="button" disabled={!!editing}
              onClick={() => setCurrency(code)}
              className={
                "flex items-center justify-between rounded-lg border px-3 py-2.5 text-left transition-all disabled:cursor-not-allowed " +
                (currency === code
                  ? "border-[var(--primary)] bg-[var(--card)] text-[var(--foreground)] shadow-sm"
                  : "border-transparent text-[var(--muted-foreground)] hover:bg-[var(--card)]/60")
              }>
              <span><b className="mr-2 font-semibold">{code}</b><span className="text-xs">{label}</span></span>
              <span className="text-lg font-semibold">{symbol}</span>
            </button>
          ))}
        </div>
        {editing && (
          <span className="text-xs text-[var(--muted-foreground)]">
            Валюта фиксируется при создании заказа.
          </span>
        )}
      </div>

      <div className="grid gap-2">
        <Label>Вид транспорта</Label>
        <div className="grid grid-cols-2 gap-2">
          {([["truck", "🚚 Трак"], ["train", "🚂 Поезд"]] as const).map(([v, label]) => (
            <button key={v} type="button" onClick={() => setTransport(v)}
              className={
                "rounded-lg border px-3 py-2 text-sm font-medium transition-colors " +
                (transport === v ? "border-[var(--primary)] bg-[var(--primary)]/5" : "hover:bg-[var(--muted)]/40")
              }>
              {label}
            </button>
          ))}
        </div>
      </div>

      {transport === "truck" && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="grid gap-2">
            <Label>Номер машины</Label>
            <LicensePlateInput value={truck} onChange={setTruck} />
          </div>
          <div className="grid gap-2">
            <Label>Дата прибытия</Label>
            <Input type="date" value={arrival} onChange={(e) => setArrival(e.target.value)} />
          </div>
        </div>
      )}

      <div className="grid gap-2">
        <Label>Позиции (в мешках)</Label>
        {rows.map((r, i) => (
          <div key={i} className="flex flex-wrap gap-2">
            <Select className="min-w-40 flex-1" value={r.product}
              onChange={(e) => {
                const product = e.target.value;
                setRows(rows.map((x, j) => j === i
                  ? { ...x, product, price: x.price || clientPrices[product] || "" } : x));
              }}>
              <option value="">Товар</option>
              {(products ?? []).map((p) => {
                const bags = p.available_bags ?? 0;
                return (
                  <option key={p.id} value={p.id} disabled={bags <= 0}>
                    {p.label}{bags > 0 ? ` · ${bags} меш.` : " — нет в наличии"}
                  </option>
                );
              })}
            </Select>
            <Input type="number" min="1" placeholder="Мешков" className="w-20 sm:w-24" value={r.quantity}
              onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} />
            <Input type="number" min="0" step="0.01"
              placeholder={`Цена, ${currency === "USD" ? "$" : "₸"}`} className="w-28 sm:w-36" value={r.price}
              onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, price: e.target.value } : x))} />
            <Button type="button" variant="ghost" size="icon"
              onClick={() => setRows(rows.length > 1 ? rows.filter((_, j) => j !== i) : rows)}>
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
        <Button type="button" variant="outline" size="sm" className="self-start"
          onClick={() => setRows([...rows, { product: "", quantity: "", price: "" }])}>
          <Plus className="size-4" /> Добавить позицию
        </Button>
      </div>

      <div className="flex items-center justify-between border-t pt-4">
        <span className="text-sm text-[var(--muted-foreground)]">Итого</span>
        <span className="text-xl font-bold tabular-nums">{formatCurrency(String(total), selectedCurrency)}</span>
      </div>
      <div className="flex items-start gap-2 rounded-lg border bg-[var(--muted)]/30 px-3 py-2.5 text-xs text-[var(--muted-foreground)]">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        {editing
          ? "Позиции и цены можно менять до начала загрузки. Изменения попадут в журнал."
          : `Цена подставляется из личного прайса клиента в ${currency}. Валюта и отдел закрепятся за заказом.`}
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>Отмена</Button>
        <Button type="submit" disabled={busy || !client || !dept || !allPriced}>
          {busy ? "Сохранение…" : editing ? "Сохранить изменения" : "Создать заказ"}
        </Button>
      </div>
    </form>
  );
}
