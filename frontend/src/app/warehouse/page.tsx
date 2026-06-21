"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Plus, Minus, History } from "lucide-react";
import type { StockItem, Product } from "@/lib/types";

interface Movement {
  id: number; product: number; product_label: string;
  delta: number; balance_after: number; reason: string;
  note: string; created_at: string; created_by_name: string | null;
}

const REASON_LABELS: Record<string, string> = {
  adjustment: "Корректировка", shipment: "Отгрузка", receipt: "Приёмка",
};

export default function WarehousePage() {
  const { data: stock, reload } = useApi<StockItem[]>("/stock/");
  const { data: products } = useApi<Product[]>("/products/");
  const { data: movements, reload: reloadMoves } =
    useApi<Movement[]>("/stock/movements/");
  const { me } = useAuth();
  const canAdjust = me?.is_superuser || me?.roles.includes("manager");

  const [product, setProduct] = useState("");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const maxBags = Math.max(1, ...(stock ?? []).map((s) => s.bags));

  async function adjust(sign: 1 | -1) {
    if (!product || !amount) return;
    setBusy(true); setError("");
    try {
      await api.post("/stock/adjust/", {
        product: Number(product),
        delta: sign * Number(amount),
        note,
      });
      setAmount(""); setNote("");
      reload(); reloadMoves();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Склад готовой муки">
      {canAdjust && (
        <Card className="mb-6">
          <CardHeader><CardTitle>Изменить остаток</CardTitle></CardHeader>
          <CardContent>
            <div className="flex items-end gap-3">
              <div className="flex flex-1 flex-col gap-1.5">
                <Label>Товар</Label>
                <Select value={product} onChange={(e) => setProduct(e.target.value)}>
                  <option value="">Выберите товар</option>
                  {(products ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </Select>
              </div>
              <div className="flex w-32 flex-col gap-1.5">
                <Label>Мешков</Label>
                <Input type="number" min="1" value={amount}
                  onChange={(e) => setAmount(e.target.value)} placeholder="0" />
              </div>
              <div className="flex flex-1 flex-col gap-1.5">
                <Label>Причина (необязательно)</Label>
                <Input value={note} onChange={(e) => setNote(e.target.value)}
                  placeholder="напр. инвентаризация, бой мешков" />
              </div>
              <Button type="button" disabled={busy || !product || !amount}
                onClick={() => adjust(1)}>
                <Plus className="size-4" /> Добавить
              </Button>
              <Button type="button" variant="outline" disabled={busy || !product || !amount}
                onClick={() => adjust(-1)}>
                <Minus className="size-4" /> Списать
              </Button>
            </div>
            {error && <p className="mt-2 text-sm text-[var(--destructive)]">{error}</p>}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-4 gap-4">
        {(stock ?? []).map((s) => (
          <Card key={s.id} className="p-6">
            <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
              {s.product_label}
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
            Склад пуст. Добавьте мешки, чтобы появились остатки.
          </p>
        )}
      </div>

      <Card className="mt-6">
        <CardHeader className="flex-row items-center gap-2">
          <History className="size-4 text-[var(--muted-foreground)]" />
          <CardTitle>История движений</CardTitle>
        </CardHeader>
        <CardContent>
          {(movements ?? []).length === 0 ? (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">
              Движений пока нет.
            </p>
          ) : (
            <Table>
              <THead>
                <TR><TH>Дата</TH><TH>Товар</TH><TH>Изменение</TH><TH>Остаток</TH>
                  <TH>Тип</TH><TH>Причина</TH><TH>Кто</TH></TR>
              </THead>
              <TBody>
                {(movements ?? []).map((m) => (
                  <TR key={m.id}>
                    <TD className="whitespace-nowrap text-[var(--muted-foreground)]">
                      {new Date(m.created_at).toLocaleString("ru-RU")}
                    </TD>
                    <TD>{m.product_label}</TD>
                    <TD className={`tabular-nums font-medium ${m.delta > 0 ? "text-[var(--success)]" : "text-[var(--destructive)]"}`}>
                      {m.delta > 0 ? "+" : ""}{m.delta}
                    </TD>
                    <TD className="tabular-nums">{m.balance_after}</TD>
                    <TD><Badge tone="muted">{REASON_LABELS[m.reason] ?? m.reason}</Badge></TD>
                    <TD className="text-[var(--muted-foreground)]">{m.note || "—"}</TD>
                    <TD className="text-[var(--muted-foreground)]">{m.created_by_name || "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </AppShell>
  );
}
