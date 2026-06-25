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
import { Trash2, Plus, Info } from "lucide-react";

interface PortalProduct { id: number; label: string; weight_kg: string; available_bags: number; }

export default function PortalNewOrderPage() {
  const router = useRouter();
  const { data: products } = useApi<PortalProduct[]>("/portal/catalog/");
  const { data: stores } = useApi<Store[]>("/portal/stores/");
  const [rows, setRows] = useState([{ product: "", quantity: "" }]);
  const [intent, setIntent] = useState<"debt" | "instant">("debt");
  const [store, setStore] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const items = rows.filter((r) => r.product && Number(r.quantity) > 0)
        .map((r) => ({ product: Number(r.product), quantity: Number(r.quantity) }));
      if (!items.length) throw new Error("empty");
      await api.post("/portal/orders/", {
        items, settlement_intent: intent, store: store ? Number(store) : null,
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
                    <option key={p.id} value={p.id}>
                      {p.label} · в наличии {p.available_bags} меш.
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
              <Label className="mb-2 block">Способ расчёта</Label>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {([
                  { v: "debt", title: "В долг", desc: "Оплата после отгрузки, поэтапно или полностью" },
                  { v: "instant", title: "Моментальная оплата", desc: "Оплата через банк после отгрузки" },
                ] as const).map((opt) => (
                  <button key={opt.v} type="button" onClick={() => setIntent(opt.v)}
                    className={
                      "flex flex-col items-start gap-0.5 rounded-lg border p-3 text-left transition-colors " +
                      (intent === opt.v
                        ? "border-[var(--primary)] bg-[var(--primary)]/5"
                        : "hover:bg-[var(--muted)]/40")
                    }>
                    <span className="text-sm font-medium">{opt.title}</span>
                    <span className="text-xs text-[var(--muted-foreground)]">{opt.desc}</span>
                  </button>
                ))}
              </div>
            </div>
            <div className="flex items-start gap-2 rounded-lg border bg-[var(--muted)]/30 px-3 py-2.5 text-xs text-[var(--muted-foreground)]">
              <Info className="mt-0.5 size-3.5 shrink-0" />
              Стоимость рассчитает оператор при подтверждении заказа — по вашим ценам.
            </div>
            {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
            <Button type="submit" disabled={busy}>{busy ? "Отправка…" : "Отправить заказ"}</Button>
          </CardContent>
        </Card>
      </form>
    </AppShell>
  );
}
