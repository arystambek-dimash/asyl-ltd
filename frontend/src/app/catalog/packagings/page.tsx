"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { Plus } from "lucide-react";
import type { Packaging } from "@/lib/types";

export default function PackagingsPage() {
  const { data: packagings, reload } = useApi<Packaging[]>("/packagings/");
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

  async function toggle(p: Packaging) {
    try { await api.patch(`/packagings/${p.id}/`, { is_active: !p.is_active }); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  return (
    <AppShell title="Фасовки">
      <Card className="mb-6 max-w-xl">
        <CardHeader><CardTitle>Новая фасовка</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={add} className="flex items-end gap-3">
            <div className="flex flex-1 flex-col gap-1.5">
              <Label>Название</Label>
              <Input placeholder="напр. 50 кг" value={name}
                onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="flex w-32 flex-col gap-1.5">
              <Label>Вес, кг</Label>
              <Input type="number" step="0.01" placeholder="50.00" value={weight}
                onChange={(e) => setWeight(e.target.value)} required />
            </div>
            <Button type="submit" disabled={busy}><Plus className="size-4" /> Добавить</Button>
          </form>
          {error && <p className="mt-2 text-sm text-[var(--destructive)]">{error}</p>}
        </CardContent>
      </Card>

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
    </AppShell>
  );
}
