"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import type { Store } from "@/lib/types";
import { Info, Plus, Trash2 } from "lucide-react";

interface PortalProduct {
  id: number; label: string; weight_kg: string; available_bags: number;
  price: string | null;
}

export default function PortalNewOrderPage() {
  const router = useRouter();
  const { data: products } = useApi<PortalProduct[]>("/portal/catalog/");
  const { data: stores } = useApi<Store[]>("/portal/stores/");
  const [rows, setRows] = useState([{ product: "", quantity: "" }]);
  const [transport, setTransport] = useState<"truck" | "train">("truck");
  const [store, setStore] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const estimatedTotal = rows.reduce((sum, row) => {
    const product = products?.find((item) => String(item.id) === row.product);
    return sum + Number(product?.price ?? 0) * Number(row.quantity || 0);
  }, 0);
  const hasUnpricedItems = rows.some((row) => row.product
    && !products?.find((item) => String(item.id) === row.product)?.price);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const items = rows.filter((r) => r.product && Number(r.quantity) > 0)
        .map((r) => ({ product: Number(r.product), quantity: Number(r.quantity) }));
      if (!items.length) throw new Error("empty");
      await api.post("/portal/orders/", {
        items, transport_type: transport,
        store: store ? Number(store) : null,
      });
      router.push("/portal/orders");
    } catch (err) {
      setError(err instanceof Error && err.message === "empty"
        ? "Добавьте хотя бы одну позицию." : apiError(err));
    } finally { setBusy(false); }
  }

  return (
    <AppShell title="Новый заказ" portal>
      <form onSubmit={submit} className="max-w-2xl">
        <Card>
          <CardHeader><CardTitle>Оформление заказа</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-4">
            {rows.map((r, i) => (
              <div key={i} className="flex gap-2">
                <Select className="flex-1" value={r.product}
                  onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, product: e.target.value } : x))}>
                  <option value="">Товар</option>
                  {(products ?? []).map((p) => (
                    <option key={p.id} value={p.id} disabled={p.available_bags <= 0}>
                      {p.label}
                      {p.available_bags > 0
                        ? ` · ${p.price ? `${Number(p.price).toLocaleString("ru-RU")} ₸ · ` : ""}в наличии ${p.available_bags} меш.`
                        : " — нет в наличии"}
                    </option>
                  ))}
                </Select>
                <Input type="number" min="1"
                  max={products?.find((p) => String(p.id) === r.product)?.available_bags || undefined}
                  placeholder="Мешков" className="w-24 sm:w-32" value={r.quantity}
                  onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} />
                <Button type="button" variant="ghost" size="icon"
                  onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 className="size-4" /></Button>
              </div>
            ))}
            <Button type="button" variant="outline" size="sm" className="self-start"
              onClick={() => setRows([...rows, { product: "", quantity: "" }])}>
              <Plus className="size-4" /> Добавить позицию
            </Button>
            {(stores?.length ?? 0) > 0 && (
              <div className="border-t pt-4">
                <Label className="mb-1.5 block">Магазин (необязательно)</Label>
                <Select value={store} onChange={(e) => setStore(e.target.value)}>
                  <option value="">Без магазина (на себя)</option>
                  {(stores ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </Select>
              </div>
            )}
            <div className="border-t pt-4">
              <Label className="mb-2 block">Вид транспорта</Label>
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
            <div className="flex items-start gap-2 rounded-lg border bg-[var(--muted)]/30 px-3 py-2.5 text-xs text-[var(--muted-foreground)]">
              <Info className="mt-0.5 size-3.5 shrink-0" />
              <span>
                {hasUnpricedItems
                  ? "Для товаров без закреплённой цены стоимость подтвердит менеджер."
                  : "Стоимость рассчитана по вашему личному прайс-листу."}
                {" Способ оплаты вы выберете после завершения отгрузки."}
                {estimatedTotal > 0 && (
                  <b className="ml-1 text-[var(--foreground)]">
                    Предварительно: {estimatedTotal.toLocaleString("ru-RU")} ₸
                  </b>
                )}
              </span>
            </div>
            {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            <Button type="submit" disabled={busy}>{busy ? "Отправка…" : "Отправить заказ"}</Button>
          </CardContent>
        </Card>
      </form>
    </AppShell>
  );
}
