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
import { formatMoney } from "@/lib/utils";
import { Trash2, Plus } from "lucide-react";
import type { Client, Product } from "@/lib/types";

export default function NewOrderPage() {
  const router = useRouter();
  const { data: clients } = useApi<Client[]>("/clients/");
  const { data: products } = useApi<Product[]>("/products/");
  const [client, setClient] = useState("");
  const [rows, setRows] = useState<{ product: string; quantity: string }[]>([
    { product: "", quantity: "" },
  ]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const total = rows.reduce((s, r) => {
    const p = products?.find((x) => String(x.id) === r.product);
    return s + (p ? Number(p.price) * Number(r.quantity || 0) : 0);
  }, 0);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const items = rows
        .filter((r) => r.product && Number(r.quantity) > 0)
        .map((r) => ({ product: Number(r.product), quantity: Number(r.quantity) }));
      if (!items.length) throw new Error("empty");
      const { data } = await api.post("/orders/", { client: Number(client), items });
      router.push(`/orders/${data.id}`);
    } catch (err) {
      setError(err instanceof Error && err.message === "empty"
        ? "Добавьте хотя бы одну позицию." : apiError(err));
    } finally { setBusy(false); }
  }

  return (
    <AppShell title="Новый заказ">
      <form onSubmit={submit} className="max-w-2xl">
        <Card>
          <CardHeader><CardTitle>Параметры заказа</CardTitle></CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <Label>Клиент</Label>
              <Select value={client} onChange={(e) => setClient(e.target.value)} required>
                <option value="">Выберите клиента</option>
                {(clients ?? []).map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </Select>
            </div>

            <div className="flex flex-col gap-2">
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
                  <Input type="number" min="1" placeholder="Мешков" className="w-32"
                    value={r.quantity}
                    onChange={(e) => setRows(rows.map((x, j) => j === i ? { ...x, quantity: e.target.value } : x))} />
                  <Button type="button" variant="ghost" size="icon"
                    onClick={() => setRows(rows.filter((_, j) => j !== i))}>
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
            <div className="flex gap-2">
              <Button type="submit" disabled={busy}>{busy ? "Создание…" : "Создать заказ"}</Button>
              <Button type="button" variant="outline" onClick={() => router.back()}>Отмена</Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </AppShell>
  );
}
