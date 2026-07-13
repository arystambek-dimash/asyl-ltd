"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Modal } from "@/components/ui/modal";
import { StatusBadge } from "@/components/status-badge";
import { Badge } from "@/components/ui/badge";
import { StatCard } from "@/components/ui/stat-card";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { ErrorAlert } from "@/components/ui/data-state";
import { PaymentChain, AddPaymentActions } from "@/components/payment-chain";
import {
  ORDER_STATUS_LABELS, PAYMENT_STATUS_LABELS, PAYMENT_STATUS_TONE,
} from "@/lib/constants";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can, deptLabel } from "@/lib/can";
import { formatMoney } from "@/lib/utils";
import { Plus, Search, Trash2, Info } from "lucide-react";
import type { Order, Client, Product } from "@/lib/types";

function CityOrdersInner() {
  const { data: orders, loading, error, reload } = useApi<Order[]>("/orders/?department=field");
  const { me } = useAuth();
  const canCreate = can(me, "dept2.create");
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");

  const list = orders ?? [];
  const active = list.filter((o) => !["shipped", "rejected", "cancelled"].includes(o.status));
  const awaitingChain = list.filter((o) => (o.pending_payments?.length ?? 0) > 0);
  // Оборот без отклонённых и отменённых заявок.
  const totalSum = list
    .filter((o) => !["rejected", "cancelled"].includes(o.status))
    .reduce((s, o) => s + Number(o.total_amount || 0), 0);

  const presentStatuses = Array.from(new Set(list.map((o) => o.status)));
  const pills = [
    { key: "all", label: "Все", count: list.length },
    ...presentStatuses.map((st) => ({
      key: st,
      label: ORDER_STATUS_LABELS[st] ?? st,
      count: list.filter((o) => o.status === st).length,
    })),
  ];

  const filtered = list.filter((o) => {
    if (status !== "all" && o.status !== status) return false;
    if (!q) return true;
    return `${o.id} ${o.client_name ?? ""}`.toLowerCase().includes(q.toLowerCase());
  });

  return (
    <AppShell title={`Заявки ${deptLabel(me, "field")}`} section={`Отдел «${deptLabel(me, "field")}»`}
      description="Заявки выездного отдела: сбор с выезда, запрос и приём оплаты у клиента."
      actions={canCreate ? (
        <Button size="sm" aria-label="Новая заявка" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Новая заявка</span>
        </Button>
      ) : undefined}>
      <section className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <StatCard label="Заявок" value={String(list.length)}
          caption={`Активных: ${active.length}`} />
        <StatCard label="Оплат в цепочке" value={String(awaitingChain.length)} />
        <StatCard label="Сумма" value={`${formatMoney(totalSum)} ₸`} accent
          className="col-span-2 sm:col-span-1" />
      </section>

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по клиенту или #ID"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <FilterDropdown label="Статус" options={pills} active={status} onChange={setStatus} />
        </div>
      </div>

      {loading ? (
        <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : error && !orders ? (
        <ErrorAlert message={error} onRetry={reload} />
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {filtered.map((o) => (
            <div key={o.id} className="flex flex-col gap-3 rounded-xl border bg-[var(--card)] p-4 shadow-card">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <Link href={`/orders/${o.id}`} className="text-sm font-semibold hover:underline">
                    Заявка #{o.id}
                  </Link>
                  <div className="text-sm text-[var(--muted-foreground)]">
                    {o.client_name || `Клиент #${o.client}`}
                  </div>
                </div>
                <div className="flex flex-wrap items-center justify-end gap-1.5">
                  <StatusBadge status={o.status} dot />
                  {o.payment_status && (
                    <Badge tone={PAYMENT_STATUS_TONE[o.payment_status] ?? "muted"}>
                      {PAYMENT_STATUS_LABELS[o.payment_status] ?? o.payment_status}
                    </Badge>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2 text-sm">
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Сумма</div>
                  <div className="font-semibold tabular-nums">{formatMoney(o.total_amount)} ₸</div>
                </div>
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Оплачено</div>
                  <div className="tabular-nums">{formatMoney(o.paid_total)} ₸</div>
                </div>
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Создана</div>
                  <div>{new Date(o.created_at).toLocaleDateString("ru-RU")}</div>
                </div>
              </div>

              <PaymentChain order={o} me={me} onChanged={reload} />
              <AddPaymentActions order={o} me={me} onChanged={reload} />
            </div>
          ))}
          {filtered.length === 0 && (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)] lg:col-span-2">
              Заявок пока нет.
            </p>
          )}
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow={`Отдел «${deptLabel(me, "field")}» · Заявка`}
        title="Новая заявка"
        description="Клиент, позиции и цены. Заявку подтверждает бухгалтер."
        className="max-w-2xl">
        {open && <CityOrderForm onCancel={() => setOpen(false)}
          onDone={() => { setOpen(false); reload(); }} />}
      </Modal>
    </AppShell>
  );
}

function CityOrderForm({ onCancel, onDone }: { onCancel: () => void; onDone: () => void }) {
  const { data: clients } = useApi<Client[]>("/clients/?department=field");
  const { data: products } = useApi<Product[]>("/products/");
  const [client, setClient] = useState("");
  const [rows, setRows] = useState<{ product: string; quantity: string; price: string }[]>(
    [{ product: "", quantity: "", price: "" }]);
  const [clientPrices, setClientPrices] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

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
      await api.post("/orders/", {
        client: Number(client),
        items: valid.map((r) => ({ product: Number(r.product), quantity: Number(r.quantity) })),
        prices: Object.fromEntries(valid.map((r) => [r.product, r.price])),
      });
      onDone();
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
        Заявка попадёт на табло бухгалтера для подтверждения. Оплату можно
        запросить и принять сразу после создания.
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onCancel}>Отмена</Button>
        <Button type="submit" disabled={busy || !client || !allPriced}>
          {busy ? "Создание…" : "Создать заявку"}
        </Button>
      </div>
    </form>
  );
}

export default function CityOrdersPage() {
  return <RequirePerm perm={["dept2.view", "dept2.view_all"]} title="Заявки Сити"><CityOrdersInner /></RequirePerm>;
}
