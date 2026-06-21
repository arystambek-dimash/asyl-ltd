"use client";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Plus, Minus, SlidersHorizontal, Search } from "lucide-react";
import type { StockItem, Product } from "@/lib/types";

// Статус остатка: нет / мало (<20 мешков) / в наличии.
function stockTone(bags: number): { tone: "destructive" | "warning" | "success"; label: string } {
  if (bags <= 0) return { tone: "destructive", label: "Нет" };
  if (bags < 20) return { tone: "warning", label: "Мало" };
  return { tone: "success", label: "В наличии" };
}

export default function WarehousePage() {
  const { data: stock, reload } = useApi<StockItem[]>("/stock/");
  const { data: products } = useApi<Product[]>("/products/");
  const { me } = useAuth();
  const canAdjust = me?.is_superuser || me?.roles.includes("manager");

  // фильтры
  const [search, setSearch] = useState("");
  const [grade, setGrade] = useState("");
  const [packaging, setPackaging] = useState("");

  // модалка корректировки
  const [open, setOpen] = useState(false);
  const [product, setProduct] = useState("");
  const [amount, setAmount] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const items = stock ?? [];
  const grades = useMemo(() => Array.from(new Set(items.map((s) => s.grade))).filter(Boolean), [items]);
  const packagings = useMemo(() => Array.from(new Set(items.map((s) => s.packaging))).filter(Boolean), [items]);

  const filtered = items.filter((s) =>
    (!search || s.product_label.toLowerCase().includes(search.toLowerCase())) &&
    (!grade || s.grade === grade) &&
    (!packaging || s.packaging === packaging)
  );

  const totalBags = filtered.reduce((sum, s) => sum + s.bags, 0);
  const totalTons = filtered.reduce((sum, s) => sum + (s.bags * Number(s.weight_kg)) / 1000, 0);

  async function adjust(sign: 1 | -1) {
    if (!product || !amount) return;
    setBusy(true); setError("");
    try {
      await api.post("/stock/adjust/", { product: Number(product), delta: sign * Number(amount) });
      setProduct(""); setAmount(""); setOpen(false);
      reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  const hasFilters = search || grade || packaging;

  return (
    <AppShell title="Остатки склада">
      {/* шапка: итоги + кнопка */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-6 text-sm">
          <span className="text-[var(--muted-foreground)]">
            Позиций: <span className="font-semibold text-[var(--foreground)]">{filtered.length}</span>
          </span>
          <span className="text-[var(--muted-foreground)]">
            Мешков: <span className="font-semibold tabular-nums text-[var(--foreground)]">{formatMoney(totalBags)}</span>
          </span>
          <span className="text-[var(--muted-foreground)]">
            Вес: <span className="font-semibold tabular-nums text-[var(--foreground)]">{totalTons.toFixed(2)} т</span>
          </span>
        </div>
        {canAdjust && (
          <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
            <SlidersHorizontal className="size-4" /> Изменить остаток
          </Button>
        )}
      </div>

      {/* фильтры */}
      <Card className="mb-4">
        <CardContent className="pt-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input className="pl-8" placeholder="Быстрый поиск по товару"
                value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <Select value={grade} onChange={(e) => setGrade(e.target.value)}>
              <option value="">Все сорта</option>
              {grades.map((g) => <option key={g} value={g}>{g}</option>)}
            </Select>
            <Select value={packaging} onChange={(e) => setPackaging(e.target.value)}>
              <option value="">Все фасовки</option>
              {packagings.map((p) => <option key={p} value={p}>{p}</option>)}
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* таблица остатков */}
      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>#</TH><TH>Товар</TH><TH>Сорт</TH><TH>Фасовка</TH>
                <TH>Остаток</TH><TH>Вес</TH><TH>Статус</TH>
              </TR>
            </THead>
            <TBody>
              {filtered.map((s) => {
                const st = stockTone(s.bags);
                const tons = (s.bags * Number(s.weight_kg)) / 1000;
                return (
                  <TR key={s.id}>
                    <TD className="text-[var(--muted-foreground)]">{s.product}</TD>
                    <TD className="font-medium">{s.product_label}</TD>
                    <TD>{s.grade}</TD>
                    <TD>{s.packaging}</TD>
                    <TD className="tabular-nums font-medium">{formatMoney(s.bags)} меш.</TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">{tons.toFixed(2)} т</TD>
                    <TD><Badge tone={st.tone}>{st.label}</Badge></TD>
                  </TR>
                );
              })}
              {filtered.length === 0 && (
                <TR><TD colSpan={7} className="py-6 text-center text-[var(--muted-foreground)]">
                  {hasFilters ? "Ничего не найдено по фильтрам." : "Склад пуст. Измените остаток, чтобы появились позиции."}
                </TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      {/* модалка корректировки */}
      <Modal open={open} onClose={() => setOpen(false)} title="Изменить остаток">
        <div className="flex flex-col gap-5">
          <div className="grid gap-2">
            <Label>Товар</Label>
            <Select value={product} onChange={(e) => setProduct(e.target.value)}>
              <option value="">Выберите товар</option>
              {(products ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </Select>
          </div>
          <div className="grid gap-2">
            <Label>Мешков</Label>
            <Input type="number" min="1" value={amount}
              onChange={(e) => setAmount(e.target.value)} placeholder="0" />
          </div>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" disabled={busy || !product || !amount}
              className="w-full sm:w-auto sm:min-w-28" onClick={() => adjust(-1)}>
              <Minus className="size-4" /> Списать
            </Button>
            <Button type="button" disabled={busy || !product || !amount}
              className="w-full sm:w-auto sm:min-w-28" onClick={() => adjust(1)}>
              <Plus className="size-4" /> Добавить
            </Button>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
