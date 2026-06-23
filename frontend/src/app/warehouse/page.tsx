"use client";
import { useMemo, useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/field";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
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
  const canAdjust = can(me, "warehouse.adjust");

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

  const [sortKey, setSortKey] = useState("product_label");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...filtered].sort((a, b) => {
    let cmp: number;
    if (sortKey === "bags") cmp = a.bags - b.bags;
    else cmp = String(a.product_label).localeCompare(String(b.product_label), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

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
    <AppShell title="Остатки склада" section="Работа" description="Остатки готовой муки по сортам и фасовкам в мешках, с расчётным весом и статусом наличия."
      actions={canAdjust ? (
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <SlidersHorizontal className="size-4" /> <span className="hidden sm:inline">Изменить остаток</span>
        </Button>
      ) : undefined}>
      {/* шапка: stat-карточки */}
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <StatCard label="Позиций" value={String(filtered.length)} />
          <StatCard label="Мешков" value={formatMoney(totalBags)} />
          <StatCard label="Вес, т" value={totalTons.toFixed(2)} accent />
        </div>
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
                <TH>#</TH>
                <SortableHeader label="Товар" sortKey="product_label" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Сорт</TH><TH>Фасовка</TH>
                <SortableHeader label="Остаток" sortKey="bags" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Вес</TH><TH>Статус</TH>
              </TR>
            </THead>
            <TBody>
              {sorted.map((s) => {
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
      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Склад · Корректировка"
        title="Изменить остаток"
        description="Добавьте приёмку или спишите мешки по товару."
        footer={
          <>
            <Button type="button" variant="outline" disabled={busy || !product || !amount}
              onClick={() => adjust(-1)}><Minus className="size-4" /> Списать</Button>
            <Button type="button" disabled={busy || !product || !amount}
              onClick={() => adjust(1)}><Plus className="size-4" /> Добавить</Button>
          </>
        }>
        <div className="flex flex-col gap-4">
          <Field label="Товар">
            <Select value={product} onChange={(e) => setProduct(e.target.value)}>
              <option value="">Выберите товар</option>
              {(products ?? []).map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </Select>
          </Field>
          <Field label="Мешков">
            <Input type="number" min="1" value={amount}
              onChange={(e) => setAmount(e.target.value)} placeholder="0" />
          </Field>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
        </div>
      </Modal>
    </AppShell>
  );
}
