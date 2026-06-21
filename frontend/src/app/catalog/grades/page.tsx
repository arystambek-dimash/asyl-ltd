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
import type { Grade } from "@/lib/types";

export default function GradesPage() {
  const { data: grades, reload } = useApi<Grade[]>("/grades/");
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function add(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/grades/", { name });
      setName(""); setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function toggle(g: Grade) {
    try { await api.patch(`/grades/${g.id}/`, { is_active: !g.is_active }); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  return (
    <AppShell title="Сорта">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{grades?.length ?? 0} сортов</p>
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить сорт
        </Button>
      </div>

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

      <Modal open={open} onClose={() => setOpen(false)} title="Новый сорт">
        <form onSubmit={add} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label>Название сорта</Label>
            <Input placeholder="напр. Премиум" value={name} autoFocus
              onChange={(e) => setName(e.target.value)} required />
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
