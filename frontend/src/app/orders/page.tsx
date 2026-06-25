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
import { LicensePlateInput, formatPlate } from "@/components/ui/license-plate-input";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { FilterPills } from "@/components/ui/filter-pills";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ORDER_STATUS_LABELS, PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE } from "@/lib/constants";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatMoney } from "@/lib/utils";
import { Plus, Trash2, Search, Info } from "lucide-react";
import type { Order, Client, Product, Store } from "@/lib/types";

export default function OrdersPage() {
  const router = useRouter();
  const { data: orders, loading, reload } = useApi<Order[]>("/orders/");
  const { me } = useAuth();
  const canCreate = can(me, "orders.create");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [sortKey, setSortKey] = useState("id");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const list = orders ?? [];
  const activeCount = list.filter(
    (o) => o.status !== "shipped" && o.status !== "cancelled"
  ).length;
  const totalSum = list.reduce((s, o) => s + Number(o.total_amount || 0), 0);

  const presentStatuses = Array.from(new Set(list.map((o) => o.status)));
  const pills = [
    { key: "all", label: "Все", count: list.length },
    ...presentStatuses.map((st) => ({
      key: st,
      label: ORDER_STATUS_LABELS[st] ?? st,
      count: list.filter((o) => o.status === st).length,
    })),
  ];

  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };

  const filtered = list.filter((o) => {
    if (status !== "all" && o.status !== status) return false;
    if (!q) return true;
    const hay = `${o.id} ${o.client_name ?? ""} ${o.truck_number ?? ""}`.toLowerCase();
    return hay.includes(q.toLowerCase());
  });

  const sorted = [...filtered].sort((a, b) => {
    let av: number | string, bv: number | string;
    if (sortKey === "amount") { av = Number(a.total_amount || 0); bv = Number(b.total_amount || 0); }
    else if (sortKey === "client") { av = a.client_name ?? ""; bv = b.client_name ?? ""; }
    else if (sortKey === "status") { av = a.status; bv = b.status; }
    else { av = a.id; bv = b.id; }
    const cmp = typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Заказы" section="Работа" description="Заказы клиентов: позиции, оплаты, машина и плановая дата прибытия на отгрузку."
      actions={canCreate ? (
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Новый заказ</span>
        </Button>
      ) : undefined}>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Всего заказов" value={String(list.length)} />
        <StatCard label="В процессе" value={String(activeCount)} />
        <StatCard label="Сумма" value={`${formatMoney(totalSum)} ₸`} accent />
      </section>

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по клиенту, номеру или #ID"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex items-center gap-2 overflow-x-auto">
          <FilterPills items={pills} active={status} onChange={setStatus} />
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          {loading ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
          ) : (
            <Table>
              <THead>
                <TR>
                  <SortableHeader label="№" sortKey="id" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Клиент" sortKey="client" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH>Машина</TH>
                  <TH>Прибытие</TH>
                  <SortableHeader label="Сумма" sortKey="amount" activeKey={sortKey} dir={sortDir} onClick={toggleSort} align="right" />
                  <TH>Оплачено</TH>
                  <SortableHeader label="Статус" sortKey="status" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                </TR>
              </THead>
              <TBody>
                {sorted.map((o) => (
                  <TR key={o.id} className="cursor-pointer"
                    onClick={() => router.push(`/orders/${o.id}`)}>
                    <TD className="font-medium">
                      <Link href={`/orders/${o.id}`} className="hover:underline"
                        onClick={(e) => e.stopPropagation()}>#{o.id}</Link>
                    </TD>
                    <TD>{o.client_name || `Клиент #${o.client}`}</TD>
                    <TD className="font-medium tabular-nums">{o.truck_number ? formatPlate(o.truck_number) : "—"}</TD>
                    <TD>{o.arrival_date ? new Date(o.arrival_date).toLocaleDateString("ru-RU") : "—"}</TD>
                    <TD className="text-right tabular-nums">{formatMoney(o.total_amount)} ₸</TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{formatMoney(o.paid_total)} ₸</TD>
                    <TD>
                      <div className="flex items-center gap-1.5">
                        <StatusBadge status={o.status} dot />
                        {o.status === "shipped" && o.payment_status && (
                          <Badge tone={PAYMENT_STATUS_TONE[o.payment_status] ?? "muted"}>
                            {PAYMENT_STATUS_LABELS[o.payment_status] ?? o.payment_status}
                          </Badge>
                        )}
                      </div>
                    </TD>
                  </TR>
                ))}
                {sorted.length === 0 && (
                  <TR><TD colSpan={7} className="py-4 text-center text-[var(--muted-foreground)]">
                    Заказов пока нет.</TD></TR>)}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Работа · Заказ"
        title="Новый заказ"
        description="Клиент, позиции и плановая дата прибытия."
        className="max-w-2xl">
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
  const { data: stores } = useApi<Store[]>("/stores/");
  const [client, setClient] = useState("");
  const [store, setStore] = useState("");
  const [transport, setTransport] = useState<"truck" | "train">("truck");
  const [truck, setTruck] = useState("");
  const [arrival, setArrival] = useState("");
  const [rows, setRows] = useState<{ product: string; quantity: string }[]>([{ product: "", quantity: "" }]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const items = rows.filter((r) => r.product && Number(r.quantity) > 0)
        .map((r) => ({ product: Number(r.product), quantity: Number(r.quantity) }));
      if (!items.length) throw new Error("empty");
      const { data } = await api.post("/orders/", {
        client: Number(client),
        store: store ? Number(store) : null,
        transport_type: transport,
        truck_number: transport === "train" ? "" : truck,
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
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label>Клиент</Label>
          <Select value={client} onChange={(e) => { setClient(e.target.value); setStore(""); }} required>
            <option value="">Выберите клиента</option>
            {(clients ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </Select>
        </div>
        {(() => {
          const clientStores = (stores ?? []).filter((s) => String(s.client) === client);
          return (
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
          );
        })()}
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
          <div key={i} className="flex gap-2">
            <Select className="flex-1" value={r.product}
              onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>
              <option value="">Товар</option>
              {(products ?? []).map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </Select>
            <Input type="number" min="1" placeholder="Мешков" className="w-24 sm:w-32" value={r.quantity}
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

      <div className="flex items-start gap-2 rounded-lg border bg-[var(--muted)]/30 px-3 py-2.5 text-xs text-[var(--muted-foreground)]">
        <Info className="mt-0.5 size-3.5 shrink-0" />
        Цены назначаются при подтверждении заказа — индивидуально для клиента.
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>Отмена</Button>
        <Button type="submit" disabled={busy}>{busy ? "Создание…" : "Создать заказ"}</Button>
      </div>
    </form>
  );
}
