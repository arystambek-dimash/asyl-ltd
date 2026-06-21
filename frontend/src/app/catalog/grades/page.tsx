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
import type { Grade } from "@/lib/types";

export default function GradesPage() {
  const { data: grades, reload } = useApi<Grade[]>("/grades/");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try { await api.post("/grades/", { name }); setName(""); reload(); }
    catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function toggle(g: Grade) {
    try { await api.patch(`/grades/${g.id}/`, { is_active: !g.is_active }); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  return (
    <AppShell title="Сорта">
      <Card className="mb-6 max-w-xl">
        <CardHeader><CardTitle>Новый сорт</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={add} className="flex items-end gap-3">
            <div className="flex flex-1 flex-col gap-1.5">
              <Label>Название сорта</Label>
              <Input placeholder="напр. Премиум" value={name}
                onChange={(e) => setName(e.target.value)} required />
            </div>
            <Button type="submit" disabled={busy}><Plus className="size-4" /> Добавить</Button>
          </form>
          {error && <p className="mt-2 text-sm text-[var(--destructive)]">{error}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead><TR><TH>Сорт</TH><TH>Статус</TH><TH></TH></TR></THead>
            <TBody>
              {(grades ?? []).map((g) => (
                <TR key={g.id}>
                  <TD className="font-medium">{g.name}</TD>
                  <TD><Badge tone={g.is_active ? "success" : "muted"}>
                    {g.is_active ? "Активен" : "Скрыт"}</Badge></TD>
                  <TD>
                    <Button size="sm" variant="outline" onClick={() => toggle(g)}>
                      {g.is_active ? "Скрыть" : "Включить"}
                    </Button>
                  </TD>
                </TR>
              ))}
              {(grades ?? []).length === 0 && (
                <TR><TD colSpan={3} className="py-4 text-center text-[var(--muted-foreground)]">
                  Сортов пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </AppShell>
  );
}
