"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatMoney } from "@/lib/utils";
import { Plus, Check, X } from "lucide-react";
import type { Grade, Packaging, Product } from "@/lib/types";

export default function ProductsPage() {
  const grades = useApi<Grade[]>("/grades/");
  const packagings = useApi<Packaging[]>("/packagings/");
  const products = useApi<Product[]>("/products/");

  return (
    <AppShell title="Товары">
      <div className="grid grid-cols-2 gap-6">
        <GradesCard reload={() => { grades.reload(); products.reload(); }} data={grades.data} />
        <PackagingsCard reload={() => { packagings.reload(); products.reload(); }} data={packagings.data} />
      </div>
      <div className="mt-6">
        <ProductsCard
          products={products.data}
          grades={grades.data ?? []}
          packagings={packagings.data ?? []}
          reload={products.reload}
        />
      </div>
    </AppShell>
  );
}

/* ---------- Сорта ---------- */
function GradesCard({ data, reload }: { data: Grade[] | null; reload: () => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try { await api.post("/grades/", { name }); setName(""); reload(); }
    catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <Card>
      <CardHeader><CardTitle>Сорта</CardTitle></CardHeader>
      <CardContent>
        <form onSubmit={add} className="mb-4 flex gap-2">
          <Input placeholder="Название сорта (напр. Премиум)" value={name}
            onChange={(e) => setName(e.target.value)} required />
          <Button type="submit" disabled={busy}><Plus className="size-4" /></Button>
        </form>
        {error && <p className="mb-2 text-sm text-[var(--destructive)]">{error}</p>}
        <div className="flex flex-wrap gap-2">
          {(data ?? []).map((g) => <Badge key={g.id} tone="muted">{g.name}</Badge>)}
          {(data ?? []).length === 0 && (
            <p className="text-sm text-[var(--muted-foreground)]">Сортов пока нет.</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/* ---------- Фасовки ---------- */
function PackagingsCard({ data, reload }: { data: Packaging[] | null; reload: () => void }) {
  const [name, setName] = useState("");
  const [weight, setWeight] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/packagings/", { name, weight_kg: weight });
      setName(""); setWeight(""); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <Card>
      <CardHeader><CardTitle>Фасовки</CardTitle></CardHeader>
      <CardContent>
        <form onSubmit={add} className="mb-4 flex gap-2">
          <Input placeholder="Название (50 кг)" value={name}
            onChange={(e) => setName(e.target.value)} required />
          <Input type="number" step="0.01" placeholder="Вес, кг" className="w-28"
            value={weight} onChange={(e) => setWeight(e.target.value)} required />
          <Button type="submit" disabled={busy}><Plus className="size-4" /></Button>
        </form>
        {error && <p className="mb-2 text-sm text-[var(--destructive)]">{error}</p>}
        <div className="flex flex-wrap gap-2">
          {(data ?? []).map((p) => (
            <Badge key={p.id} tone="muted">{p.name} · {p.weight_kg} кг</Badge>
          ))}
          {(data ?? []).length === 0 && (
            <p className="text-sm text-[var(--muted-foreground)]">Фасовок пока нет.</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/* ---------- Товары ---------- */
function ProductsCard({
  products, grades, packagings, reload,
}: {
  products: Product[] | null;
  grades: Grade[]; packagings: Packaging[];
  reload: () => void;
}) {
  const [grade, setGrade] = useState("");
  const [packaging, setPackaging] = useState("");
  const [price, setPrice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState("");

  const label = (p: Product) =>
    p.label ||
    `${grades.find((g) => g.id === p.grade)?.name ?? ""} ${packagings.find((k) => k.id === p.packaging)?.name ?? ""}`;

  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/products/", {
        grade: Number(grade), packaging: Number(packaging), price,
      });
      setGrade(""); setPackaging(""); setPrice(""); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function savePrice(p: Product) {
    try {
      await api.patch(`/products/${p.id}/`, { price: editPrice });
      setEditId(null); reload();
    } catch (e) { setError(apiError(e)); }
  }

  async function toggleActive(p: Product) {
    try { await api.patch(`/products/${p.id}/`, { is_active: !p.is_active }); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  return (
    <Card>
      <CardHeader><CardTitle>Товары</CardTitle></CardHeader>
      <CardContent>
        <form onSubmit={add} className="mb-5 flex items-end gap-3">
          <div className="flex flex-1 flex-col gap-1.5">
            <Label>Сорт</Label>
            <Select value={grade} onChange={(e) => setGrade(e.target.value)} required>
              <option value="">Выберите сорт</option>
              {grades.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </Select>
          </div>
          <div className="flex flex-1 flex-col gap-1.5">
            <Label>Фасовка</Label>
            <Select value={packaging} onChange={(e) => setPackaging(e.target.value)} required>
              <option value="">Выберите фасовку</option>
              {packagings.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
            </Select>
          </div>
          <div className="flex w-40 flex-col gap-1.5">
            <Label>Цена за мешок, ₸</Label>
            <Input type="number" step="0.01" value={price}
              onChange={(e) => setPrice(e.target.value)} required />
          </div>
          <Button type="submit" disabled={busy}><Plus className="size-4" /> Создать товар</Button>
        </form>
        {error && <p className="mb-3 text-sm text-[var(--destructive)]">{error}</p>}

        {grades.length === 0 || packagings.length === 0 ? (
          <p className="py-4 text-center text-sm text-[var(--muted-foreground)]">
            Сначала создайте хотя бы один сорт и одну фасовку выше.
          </p>
        ) : (
          <Table>
            <THead>
              <TR><TH>Товар</TH><TH>Цена</TH><TH>Статус</TH><TH></TH></TR>
            </THead>
            <TBody>
              {(products ?? []).map((p) => (
                <TR key={p.id}>
                  <TD className="font-medium">{label(p)}</TD>
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
                    <Badge tone={p.is_active ? "success" : "muted"}>
                      {p.is_active ? "Активен" : "Скрыт"}
                    </Badge>
                  </TD>
                  <TD>
                    <Button size="sm" variant="outline" onClick={() => toggleActive(p)}>
                      {p.is_active ? "Скрыть" : "Включить"}
                    </Button>
                  </TD>
                </TR>
              ))}
              {(products ?? []).length === 0 && (
                <TR><TD colSpan={4} className="py-4 text-center text-[var(--muted-foreground)]">
                  Товаров пока нет.
                </TD></TR>
              )}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
