"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Plus } from "lucide-react";
import type { StockItem, Product } from "@/lib/types";

export default function WarehousePage() {
  const { data: stock, reload } = useApi<StockItem[]>("/stock/");
  const { data: products } = useApi<Product[]>("/products/");
  const { me } = useAuth();
  const canReceive = me?.is_superuser || me?.roles.includes("manager");
  const [product, setProduct] = useState("");
  const [bags, setBags] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const maxBags = Math.max(1, ...(stock ?? []).map((s) => s.bags));
  const label = (pid: number) => products?.find((p) => p.id === pid)?.label ?? `Товар #${pid}`;

  async function receive(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/stock/receipts/", { product: Number(product), bags: Number(bags) });
      setProduct(""); setBags(""); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Склад готовой муки">
      {canReceive && (
        <Card className="mb-6">
          <CardHeader><CardTitle>Приёмка муки</CardTitle></CardHeader>
          <CardContent>
            <form onSubmit={receive} className="flex items-end gap-3">
              <div className="flex flex-1 flex-col gap-1.5">
                <Label>Товар</Label>
                <Select value={product} onChange={(e) => setProduct(e.target.value)} required>
                  <option value="">Выберите товар</option>
                  {(products ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </Select>
              </div>
              <div className="flex w-40 flex-col gap-1.5">
                <Label>Мешков</Label>
                <Input type="number" min="1" value={bags} onChange={(e) => setBags(e.target.value)} required />
              </div>
              <Button type="submit" disabled={busy}><Plus className="size-4" /> Принять</Button>
            </form>
            {error && <p className="mt-2 text-sm text-[var(--destructive)]">{error}</p>}
          </CardContent>
        </Card>
      )}
      <div className="grid grid-cols-4 gap-4">
        {(stock ?? []).map((s) => (
          <Card key={s.id} className="p-6">
            <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
              {label(s.product)}
            </div>
            <div className="mt-2 text-2xl font-bold tabular-nums">{formatMoney(s.bags)}</div>
            <div className="text-xs text-[var(--muted-foreground)]">мешков</div>
            <div className="mt-3 h-1 w-full rounded-full bg-[var(--muted)]">
              <div className="h-1 rounded-full bg-[var(--primary)]"
                style={{ width: `${(s.bags / maxBags) * 100}%` }} />
            </div>
          </Card>
        ))}
        {(stock ?? []).length === 0 && (
          <p className="col-span-4 py-6 text-center text-sm text-[var(--muted-foreground)]">
            Склад пуст. Примите муку, чтобы появились остатки.
          </p>
        )}
      </div>
    </AppShell>
  );
}
