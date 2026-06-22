"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { Plus } from "lucide-react";
import type { Packaging } from "@/lib/types";

export default function PackagingsPage() {
  const { data: packagings, reload } = useApi<Packaging[]>("/packagings/");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [weight, setWeight] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/packagings/", { name, weight_kg: weight });
      setName(""); setWeight(""); setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function toggle(p: Packaging) {
    try { await api.patch(`/packagings/${p.id}/`, { is_active: !p.is_active }); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = packagings ?? [];
  const activeN = list.filter((p) => p.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...list].sort((a, b) => {
    let cmp: number;
    if (sortKey === "weight") cmp = Number(a.weight_kg) - Number(b.weight_kg);
    else cmp = a.name.localeCompare(b.name, "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Фасовки" section="Номенклатура" description="Справочник фасовок с весом мешка."
      actions={
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить фасовку</span>
        </Button>
      }>
      <div className="mb-4">
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего фасовок" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead><TR>
              <SortableHeader label="Фасовка" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <SortableHeader label="Вес" sortKey="weight" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <TH>Статус</TH><TH></TH>
            </TR></THead>
            <TBody>
              {sorted.map((p) => (
                <TR key={p.id}>
                  <TD className="font-medium">{p.name}</TD>
                  <TD className="tabular-nums">{p.weight_kg} кг</TD>
                  <TD><Badge tone={p.is_active ? "success" : "muted"}>
                    {p.is_active ? "Активна" : "Скрыта"}</Badge></TD>
                  <TD>
                    <Button size="sm" variant="outline" onClick={() => toggle(p)}>
                      {p.is_active ? "Скрыть" : "Включить"}
                    </Button>
                  </TD>
                </TR>
              ))}
              {sorted.length === 0 && (
                <TR><TD colSpan={4} className="py-4 text-center text-[var(--muted-foreground)]">
                  Фасовок пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новая фасовка">
        <form onSubmit={add} className="flex flex-col gap-5">
          <div className="grid gap-2">
            <Label>Название</Label>
            <Input placeholder="напр. 50 кг" value={name} autoFocus
              onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="grid gap-2">
            <Label>Вес, кг</Label>
            <Input type="number" step="0.01" placeholder="50.00" value={weight}
              onChange={(e) => setWeight(e.target.value)} required />
          </div>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" className="w-full sm:w-auto sm:min-w-28" disabled={busy}>{busy ? "Сохранение…" : "Добавить"}</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
