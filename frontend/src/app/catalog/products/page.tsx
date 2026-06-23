"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Plus, Check, X } from "lucide-react";
import type { Product } from "@/lib/types";

export default function ProductsPage() {
  const { data: products, reload } = useApi<Product[]>("/products/");

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [color, setColor] = useState("Red");
  const [weight, setWeight] = useState("50");
  const [price, setPrice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState("");

  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/products/", { name, color, weight_kg: weight, price });
      setName(""); setColor("Red"); setWeight("50"); setPrice(""); setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function savePrice(p: Product) {
    try { await api.patch(`/products/${p.id}/`, { price: editPrice }); setEditId(null); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  async function toggleActive(p: Product) {
    try { await api.patch(`/products/${p.id}/`, { is_active: !p.is_active }); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = products ?? [];
  const activeN = list.filter((p) => p.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...list].sort((a, b) => {
    let cmp: number;
    if (sortKey === "price") cmp = Number(a.price) - Number(b.price);
    else cmp = a.name.localeCompare(b.name, "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Товары" section="Работа" description="Товары: сорт, цвет (тип) и фасовка. Управляйте ценами и активностью."
      actions={
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Создать товар</span>
        </Button>
      }>
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего товаров" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead><TR>
              <SortableHeader label="Название" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <TH>Цвет</TH>
              <TH>Фасовка</TH>
              <SortableHeader label="Цена" sortKey="price" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <TH>Статус</TH><TH></TH>
            </TR></THead>
            <TBody>
              {sorted.map((p) => (
                <TR key={p.id}>
                  <TD className="font-medium">{p.name}</TD>
                  <TD>{p.color_label}</TD>
                  <TD className="tabular-nums">{Number(p.weight_kg)} кг</TD>
                  <TD className="tabular-nums">
                    {editId === p.id ? (
                      <div className="flex items-center gap-2">
                        <Input type="number" step="0.01" className="h-8 w-32"
                          value={editPrice} onChange={(e) => setEditPrice(e.target.value)} />
                        <Button size="sm" onClick={() => savePrice(p)}><Check className="size-4" /></Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditId(null)}><X className="size-4" /></Button>
                      </div>
                    ) : (
                      <button className="hover:underline"
                        onClick={() => { setEditId(p.id); setEditPrice(p.price); }}>
                        {formatMoney(p.price)} ₸
                      </button>
                    )}
                  </TD>
                  <TD><Badge tone={p.is_active ? "success" : "muted"}>
                    {p.is_active ? "Активен" : "Скрыт"}</Badge></TD>
                  <TD>
                    <Button size="sm" variant="outline" onClick={() => toggleActive(p)}>
                      {p.is_active ? "Скрыть" : "Включить"}
                    </Button>
                  </TD>
                </TR>
              ))}
              {sorted.length === 0 && (
                <TR><TD colSpan={6} className="py-4 text-center text-[var(--muted-foreground)]">
                  Товаров пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый товар">
        <form onSubmit={add} className="flex flex-col gap-5">
          <div className="grid gap-2">
            <Label>Название</Label>
            <Input value={name} autoFocus placeholder="напр. Высший сорт"
              onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="grid gap-2">
            <Label>Цвет (тип)</Label>
            <Select value={color} onChange={(e) => setColor(e.target.value)}>
              <option value="Red">Красный</option>
              <option value="Green">Зелёный</option>
              <option value="Blue">Синий</option>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label>Фасовка</Label>
            <Select value={weight} onChange={(e) => setWeight(e.target.value)}>
              <option value="50">50 кг</option>
              <option value="25">25 кг</option>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label>Цена за мешок, ₸</Label>
            <Input type="number" step="0.01" value={price}
              onChange={(e) => setPrice(e.target.value)} required />
          </div>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" className="w-full sm:w-auto sm:min-w-28" disabled={busy}>{busy ? "Создание…" : "Создать"}</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
