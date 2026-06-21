"use client";
import { useState } from "react";
import Link from "next/link";
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
  const { data: grades } = useApi<Grade[]>("/grades/");
  const { data: packagings } = useApi<Packaging[]>("/packagings/");
  const { data: products, reload } = useApi<Product[]>("/products/");

  const [grade, setGrade] = useState("");
  const [packaging, setPackaging] = useState("");
  const [price, setPrice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [editId, setEditId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState("");

  const activeGrades = (grades ?? []).filter((g) => g.is_active);
  const activePackagings = (packagings ?? []).filter((p) => p.is_active);
  const ready = activeGrades.length > 0 && activePackagings.length > 0;

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
    try { await api.patch(`/products/${p.id}/`, { price: editPrice }); setEditId(null); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  async function toggleActive(p: Product) {
    try { await api.patch(`/products/${p.id}/`, { is_active: !p.is_active }); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  return (
    <AppShell title="Товары">
      <Card className="mb-6">
        <CardHeader><CardTitle>Новый товар</CardTitle></CardHeader>
        <CardContent>
          {!ready ? (
            <p className="py-2 text-sm text-[var(--muted-foreground)]">
              Сначала добавьте хотя бы один{" "}
              <Link href="/catalog/grades" className="text-[var(--primary)] underline">сорт</Link>{" "}
              и одну{" "}
              <Link href="/catalog/packagings" className="text-[var(--primary)] underline">фасовку</Link>.
            </p>
          ) : (
            <form onSubmit={add} className="flex items-end gap-3">
              <div className="flex flex-1 flex-col gap-1.5">
                <Label>Сорт</Label>
                <Select value={grade} onChange={(e) => setGrade(e.target.value)} required>
                  <option value="">Выберите сорт</option>
                  {activeGrades.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                </Select>
              </div>
              <div className="flex flex-1 flex-col gap-1.5">
                <Label>Фасовка</Label>
                <Select value={packaging} onChange={(e) => setPackaging(e.target.value)} required>
                  <option value="">Выберите фасовку</option>
                  {activePackagings.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
                </Select>
              </div>
              <div className="flex w-40 flex-col gap-1.5">
                <Label>Цена за мешок, ₸</Label>
                <Input type="number" step="0.01" value={price}
                  onChange={(e) => setPrice(e.target.value)} required />
              </div>
              <Button type="submit" disabled={busy}><Plus className="size-4" /> Создать</Button>
            </form>
          )}
          {error && <p className="mt-2 text-sm text-[var(--destructive)]">{error}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead><TR><TH>Товар</TH><TH>Цена</TH><TH>Статус</TH><TH></TH></TR></THead>
            <TBody>
              {(products ?? []).map((p) => (
                <TR key={p.id}>
                  <TD className="font-medium">{p.label}</TD>
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
              {(products ?? []).length === 0 && (
                <TR><TD colSpan={4} className="py-4 text-center text-[var(--muted-foreground)]">
                  Товаров пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </AppShell>
  );
}
