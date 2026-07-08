"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { LicensePlateInput } from "@/components/ui/license-plate-input";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can, deptLabel } from "@/lib/can";
import { formatMoney } from "@/lib/utils";

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
  const { me } = useAuth();
  const { data: clients } = useApi<Client[]>("/clients/");
  const { data: products } = useApi<Product[]>("/products/");
  const { data: stores } = useApi<Store[]>("/stores/");
  // Отдел показываем тем, кто видит оба; остальным список клиентов и так обрезан.
  const showDept = can(me, "dept2.view_all");
  const [dept, setDept] = useState<Department>(editing?.department ?? "main");
  const [client, setClient] = useState(editing ? String(editing.client) : "");
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

  const visibleClients = (clients ?? []).filter(
    (c) => !showDept || c.department === dept);

  // При выборе клиента подтягиваем его цены и предзаполняем строки.
  useEffect(() => {
    if (!client) { setClientPrices({}); return; }
    api.get<Record<string, string>>(`/client-prices/?client=${client}`)
      .then((r) => {
        setClientPrices(r.data);
        setRows((rs) => rs.map((row) =>
          row.product && !row.price && r.data[row.product]
            ? { ...row, price: r.data[row.product] } : row));
      })
      .catch(() => setClientPrices({}));
  }, [client]);

  const total = rows.reduce((s, r) => s + Number(r.price || 0) * Number(r.quantity || 0), 0);
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
        store: store ? Number(store) : null,
        transport_type: transport,
        truck_number: transport === "train" ? "" : truck,
        arrival_date: arrival || null,
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
      {showDept && !editing && (
        <div className="grid gap-2">
          <Label>Отдел продаж</Label>
          <div className="grid grid-cols-2 gap-2">
            {(["main", "field"] as const).map((d) => (
              <button key={d} type="button"
                onClick={() => { setDept(d); setClient(""); setStore(""); }}
                className={
                  "rounded-lg border px-3 py-2 text-sm font-medium transition-colors " +
                  (dept === d ? "border-[var(--primary)] bg-[var(--primary)]/5" : "hover:bg-[var(--muted)]/40")
                }>
                {deptLabel(me, d)}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label>Клиент</Label>
          <Select value={client} disabled={!!editing}
            onChange={(e) => { setClient(e.target.value); setStore(""); }} required>
            <option value="">Выберите клиента</option>
            {(editing ? (clients ?? []) : visibleClients).map((c) => (
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
            <Input type="number" min="0" placeholder="Цена/мешок" className="w-28 sm:w-36" value={r.price}
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
        <span className="text-xl font-bold tabular-nums">{formatMoney(String(total))} ₸</span>
      </div>
      <div className="flex items-start gap-2 rounded-lg border bg-[var(--muted)]/30 px-3 py-2.5 text-xs text-[var(--muted-foreground)]">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        {editing
          ? "Позиции и цены можно менять до начала загрузки. Изменения попадут в журнал."
          : dept === "field"
            ? "Заявка отдела «Сити» попадёт на табло бухгалтера для подтверждения."
            : "Цена подставляется из прайса клиента. Заказ создаётся сразу подтверждённым."}
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>Отмена</Button>
        <Button type="submit" disabled={busy || !client || !allPriced}>
          {busy ? "Сохранение…" : editing ? "Сохранить изменения" : "Создать заказ"}
        </Button>
      </div>
    </form>
  );
}
