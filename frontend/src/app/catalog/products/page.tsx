"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
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
import { Tabs } from "@/components/ui/tabs";
import { ErrorAlert } from "@/components/ui/data-state";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Plus, Check, X, Pencil, Archive, ArchiveRestore } from "lucide-react";
import type { Product } from "@/lib/types";

function ProductsPageInner() {
  const { data: products, error: loadError, reload } = useApi<Product[]>("/products/");
  const { data: archived, reload: reloadArchived } = useApi<Product[]>("/products/?archived=1");
  const { me } = useAuth();
  const canEdit = can(me, "catalog.edit");

  const [tab, setTab] = useState<"active" | "archive">("active");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Product | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState("Red");
  const [weight, setWeight] = useState("50");
  const [price, setPrice] = useState("");
  const [askWeight, setAskWeight] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState("");
  const [arcItem, setArcItem] = useState<Product | null>(null);
  const [arcError, setArcError] = useState("");
  const [arcBusy, setArcBusy] = useState(false);

  function openNew() {
    setEditing(null); setName(""); setColor("Red"); setWeight("50"); setPrice("");
    setAskWeight(false); setError(""); setOpen(true);
  }
  function openEdit(p: Product) {
    setEditing(p); setName(p.name); setColor(p.color);
    setWeight(String(Number(p.weight_kg))); setPrice(p.price);
    setAskWeight(p.ask_truck_weight ?? false);
    setError(""); setOpen(true);
  }

  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const body = { name, color, weight_kg: weight, price, ask_truck_weight: askWeight };
      if (editing) await api.patch(`/products/${editing.id}/`, body);
      else await api.post("/products/", body);
      setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function confirmArchive() {
    if (!arcItem) return;
    setArcBusy(true); setArcError("");
    try {
      await api.post(`/products/${arcItem.id}/archive/`);
      setArcItem(null); reload(); reloadArchived();
    } catch (e) { setArcError(apiError(e)); } finally { setArcBusy(false); }
  }

  async function restore(p: Product) {
    try { await api.post(`/products/${p.id}/restore/`); reload(); reloadArchived(); }
    catch (e) { setError(apiError(e)); }
  }

  async function savePrice(p: Product) {
    try { await api.patch(`/products/${p.id}/`, { price: editPrice }); setEditId(null); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = products ?? [];
  const archiveList = archived ?? [];
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
    <AppShell title="Товары" section="Работа" description="Товары: сорт, цвет (тип) и фасовка. Управляйте ценами и архивом."
      actions={
        <Button size="sm" onClick={openNew} aria-label="Создать товар">
          <Plus className="size-4" /> <span className="hidden sm:inline">Создать товар</span>
        </Button>
      }>
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Активных товаров" value={String(list.length)} accent />
          <StatCard label="В архиве" value={String(archiveList.length)} />
        </div>
      </div>

      <div className="mb-4">
        <Tabs variant="bar" active={tab} onChange={(k) => setTab(k as "active" | "archive")}
          tabs={[
            { key: "active", label: "Товары", icon: Check },
            { key: "archive", label: "Архив", icon: Archive },
          ]} />
      </div>

      {loadError && !products && <div className="mb-4"><ErrorAlert message={loadError} onRetry={reload} /></div>}

      {tab === "archive" ? (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead><TR>
                <TH>Название</TH><TH>Цвет</TH><TH>Фасовка</TH><TH>Цена</TH><TH></TH>
              </TR></THead>
              <TBody>
                {archiveList.map((p) => (
                  <TR key={p.id}>
                    <TD className="font-medium">{p.name}</TD>
                    <TD>{p.color_label}</TD>
                    <TD className="tabular-nums">{Number(p.weight_kg)} кг</TD>
                    <TD className="tabular-nums">{formatMoney(p.price)} ₸</TD>
                    <TD>
                      <div className="flex items-center justify-end gap-1">
                        <Badge tone="muted">В архиве</Badge>
                        {canEdit && (
                          <Button size="sm" variant="outline" onClick={() => restore(p)}>
                            <ArchiveRestore className="size-4" /> Восстановить
                          </Button>
                        )}
                      </div>
                    </TD>
                  </TR>
                ))}
                {archiveList.length === 0 && (
                  <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">
                    Архив пуст.</TD></TR>
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead><TR>
                <SortableHeader label="Название" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Цвет</TH>
                <TH>Фасовка</TH>
                <SortableHeader label="Цена" sortKey="price" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH></TH>
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
                    <TD>
                      <div className="flex items-center justify-end gap-1">
                        {canEdit && (
                          <Button size="sm" variant="ghost" onClick={() => openEdit(p)} title="Изменить">
                            <Pencil className="size-4" />
                          </Button>
                        )}
                        {canEdit && (
                          <Button size="sm" variant="ghost"
                            className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                            onClick={() => { setArcError(""); setArcItem(p); }} title="В архив">
                            <Archive className="size-4" />
                          </Button>
                        )}
                      </div>
                    </TD>
                  </TR>
                ))}
                {sorted.length === 0 && (
                  <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">
                    Товаров пока нет.</TD></TR>
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

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
          <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border p-3">
            <input type="checkbox" className="mt-0.5 size-4 accent-[var(--primary)]"
              checked={askWeight} onChange={(e) => setAskWeight(e.target.checked)} />
            <span className="text-sm">
              <span className="font-medium">Спрашивать вес машины при въезде</span>
              <span className="block text-xs text-[var(--muted-foreground)]">
                Если выключено — вес не спрашивается, берётся расчётный по мешкам.
              </span>
            </span>
          </label>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
        </form>
      </Modal>

      <ConfirmDialog
        open={!!arcItem}
        onClose={() => setArcItem(null)}
        title="Отправить товар в архив?"
        description={arcItem ? `«${arcItem.label}» уйдёт в архив: пропадёт из выбора новых заказов. Старые заказы и отчёты не изменятся. Можно восстановить.` : ""}
        busy={arcBusy}
        error={arcError}
        onConfirm={confirmArchive}
      />
    </AppShell>
  );
}

export default function ProductsPage() {
  return <RequirePerm perm="catalog.view" title="Товары"><ProductsPageInner /></RequirePerm>;
}
