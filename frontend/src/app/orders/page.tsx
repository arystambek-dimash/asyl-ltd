"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Modal } from "@/components/ui/modal";
import { StatusBadge } from "@/components/status-badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatMoney } from "@/lib/utils";
import { Plus, Trash2 } from "lucide-react";
import type { Order, Client, Product } from "@/lib/types";

export default function OrdersPage() {
  const { data: orders, loading, reload } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const canCreate = can(me, "orders.create");
  const [open, setOpen] = useState(false);

  return (
    <AppShell title="Заказы">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{orders?.length ?? 0} заказов</p>
        {canCreate && (
          <Button size="sm" onClick={() => setOpen(true)}>
            <Plus className="size-4" /> Новый заказ
          </Button>
        )}
      </div>
      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : (
            <Table>
              <THead>
                <TR><TH>№</TH><TH>Клиент</TH><TH>Машина</TH><TH>Прибытие</TH>
                  <TH>Сумма</TH><TH>Оплачено</TH><TH>Статус</TH></TR>
              </THead>
              <TBody>
                {(orders ?? []).map((o) => (
                  <TR key={o.id}>
                    <TD className="font-medium">
                      <Link href={`/orders/${o.id}`} className="hover:underline">#{o.id}</Link>
                    </TD>
                    <TD>{o.client_name || `Клиент #${o.client}`}</TD>
                    <TD>{o.truck_number || "—"}</TD>
                    <TD>{o.arrival_date ? new Date(o.arrival_date).toLocaleDateString("ru-RU") : "—"}</TD>
                    <TD className="tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{formatMoney(o.paid_total)} ₸</TD>
                    <TD><StatusBadge status={o.status} /></TD>
                  </TR>
                ))}
                {(orders ?? []).length === 0 && (
                  <TR><TD colSpan={7} className="py-4 text-center text-[var(--muted-foreground)]">
                    Заказов пока нет.</TD></TR>)}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый заказ" className="max-w-2xl">
        {open && <NewOrderForm onCancel={() => setOpen(false)}
          onDone={() => { setOpen(false); reload(); }} />}
      </Modal>
    </AppShell>
  );
}

function NewOrderForm({ onCancel, onDone }: { onCancel: () => void; onDone: () => void }) {
  const router = useRouter();
  const { data: clients } = useApi<Client[]>("/clients/");
  const { data: products } = useApi<Product[]>("/products/");
  const [client, setClient] = useState("");
  const [truck, setTruck] = useState("");
  const [arrival, setArrival] = useState("");
  const [rows, setRows] = useState<{ product: string; quantity: string }[]>([{ product: "", quantity: "" }]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const total = rows.reduce((s, r) => {
    const p = products?.find((x) => String(x.id) === r.product);
    return s + (p ? Number(p.price) * Number(r.quantity || 0) : 0);
  }, 0);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const items = rows.filter((r) => r.product && Number(r.quantity) > 0)
        .map((r) => ({ product: Number(r.product), quantity: Number(r.quantity) }));
      if (!items.length) throw new Error("empty");
      const { data } = await api.post("/orders/", {
        client: Number(client),
        truck_number: truck,
        arrival_date: arrival || null,
        items,
      });
      onDone();
      router.push(`/orders/${data.id}`);
    } catch (err) {
      setError(err instanceof Error && err.message === "empty"
        ? "Добавьте хотя бы одну позицию." : apiError(err));
    } finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-5">
      <div className="grid gap-2">
        <Label>Клиент</Label>
        <Select value={client} onChange={(e) => setClient(e.target.value)} required>
          <option value="">Выберите клиента</option>
          {(clients ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </Select>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="grid gap-2">
          <Label>Номер машины</Label>
          <Input value={truck} placeholder="напр. 01A777"
            onChange={(e) => setTruck(e.target.value.toUpperCase())} />
        </div>
        <div className="grid gap-2">
          <Label>Дата прибытия</Label>
          <Input type="date" value={arrival} onChange={(e) => setArrival(e.target.value)} />
        </div>
      </div>

      <div className="grid gap-2">
        <Label>Позиции (в мешках)</Label>
        {rows.map((r, i) => (
          <div key={i} className="flex gap-2">
            <Select className="flex-1" value={r.product}
              onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>
              <option value="">Товар</option>
              {(products ?? []).map((p) => (
                <option key={p.id} value={p.id}>{p.label} — {formatMoney(p.price)} ₸</option>
              ))}
            </Select>
            <Input type="number" min="1" placeholder="Мешков" className="w-32" value={r.quantity}
              onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} />
            <Button type="button" variant="ghost" size="icon"
              onClick={() => setRows(rows.length > 1 ? rows.filter((_, j) => j !== i) : rows)}>
              <Trash2 className="size-4" />
            </Button>
          </div>
        ))}
        <Button type="button" variant="outline" size="sm" className="self-start"
          onClick={() => setRows([...rows, { product: "", quantity: "" }])}>
          <Plus className="size-4" /> Добавить позицию
        </Button>
      </div>

      <div className="flex items-center justify-between border-t pt-4">
        <span className="text-sm text-[var(--muted-foreground)]">Итого</span>
        <span className="text-xl font-bold tabular-nums">{formatMoney(total)} ₸</span>
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>Отмена</Button>
        <Button type="submit" disabled={busy}>{busy ? "Создание…" : "Создать заказ"}</Button>
      </div>
    </form>
  );
}
