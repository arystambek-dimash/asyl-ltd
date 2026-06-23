"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Plus, Check, X, Pencil, Trash2 } from "lucide-react";
import type { Product } from "@/lib/types";

export default function ProductsPage() {
  const { data: products, reload } = useApi<Product[]>("/products/");
  const { me } = useAuth();
  const canEdit = can(me, "catalog.edit");
  const canDelete = can(me, "catalog.delete");

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState("Red");
  const [weight, setWeight] = useState("50");
  const [price, setPrice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState("");
  const [delItem, setDelItem] = useState<Product | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);

  function openNew() {
    setEditing(null); setName(""); setColor("Red"); setWeight("50"); setPrice("");
    setError(""); setOpen(true);
  }
  function openEdit(p: Product) {
    setEditing(p); setName(p.name); setColor(p.color);
    setWeight(String(Number(p.weight_kg))); setPrice(p.price);
    setError(""); setOpen(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const body = { name, color, weight_kg: weight, price };
      if (editing) await api.patch(`/products/${editing.id}/`, body);
      else await api.post("/products/", body);
      setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true); setDelError("");
    try {
      await api.delete(`/products/${delItem.id}/`);
      setDelItem(null); reload();
    } catch (e) { setDelError(apiError(e)); } finally { setDelBusy(false); }
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
        <Button size="sm" onClick={openNew}>
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
                    <div className="flex items-center justify-end gap-1">
                      <Button size="sm" variant="outline" onClick={() => toggleActive(p)}>
                        {p.is_active ? "Скрыть" : "Включить"}
                      </Button>
                      {canEdit && (
                        <Button size="sm" variant="ghost" onClick={() => openEdit(p)} title="Изменить">
                          <Pencil className="size-4" />
                        </Button>
                      )}
                      {canDelete && (
                        <Button size="sm" variant="ghost"
                          className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                          onClick={() => { setDelError(""); setDelItem(p); }} title="Удалить">
                          <Trash2 className="size-4" />
                        </Button>
                      )}
                    </div>
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

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow={editing ? "Номенклатура · Изменение" : "Номенклатура · Товар"}
        title={editing ? "Изменить товар" : "Новый товар"}
        description="Сорт, цвет (тип) и фасовка."
        footer={
          <>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" form="product-form" disabled={busy}>
              {busy ? "Сохранение…" : editing ? "Сохранить" : "Создать"}</Button>
          </>
        }>
        <form id="product-form" onSubmit={save} className="flex flex-col gap-4">
          <Field label="Название">
            <Input value={name} autoFocus placeholder="напр. Высший сорт"
              onChange={(e) => setName(e.target.value)} required />
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Цвет (тип)">
              <Select value={color} onChange={(e) => setColor(e.target.value)}>
                <option value="Red">Красный</option>
                <option value="Green">Зелёный</option>
                <option value="Blue">Синий</option>
              </Select>
            </Field>
            <Field label="Фасовка">
              <Select value={weight} onChange={(e) => setWeight(e.target.value)}>
                <option value="50">50 кг</option>
                <option value="25">25 кг</option>
              </Select>
            </Field>
          </div>
          <Field label="Цена за мешок, ₸">
            <Input type="number" step="0.01" value={price}
              onChange={(e) => setPrice(e.target.value)} required />
          </Field>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
        </form>
      </Modal>

      <ConfirmDialog
        open={!!delItem}
        onClose={() => setDelItem(null)}
        title="Удалить товар?"
        description={delItem ? `«${delItem.label}» будет удалён. Действие необратимо.` : ""}
        busy={delBusy}
        error={delError}
        onConfirm={confirmDelete}
      />
    </AppShell>
  );
}
