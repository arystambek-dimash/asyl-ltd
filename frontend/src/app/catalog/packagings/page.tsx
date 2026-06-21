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

  return (
    <AppShell title="Фасовки">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{packagings?.length ?? 0} фасовок</p>
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить фасовку
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead><TR><TH>Фасовка</TH><TH>Вес</TH><TH>Статус</TH><TH></TH></TR></THead>
            <TBody>
              {(packagings ?? []).map((p) => (
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
              {(packagings ?? []).length === 0 && (
                <TR><TD colSpan={4} className="py-4 text-center text-[var(--muted-foreground)]">
                  Фасовок пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новая фасовка">
        <form onSubmit={add} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label>Название</Label>
            <Input placeholder="напр. 50 кг" value={name} autoFocus
              onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Вес, кг</Label>
            <Input type="number" step="0.01" placeholder="50.00" value={weight}
              onChange={(e) => setWeight(e.target.value)} required />
          </div>
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" disabled={busy}>{busy ? "Сохранение…" : "Добавить"}</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
