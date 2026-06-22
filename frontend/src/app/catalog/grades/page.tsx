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

  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = grades ?? [];
  const activeN = list.filter((g) => g.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...list].sort((a, b) => {
    const cmp = a.name.localeCompare(b.name, "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Сорта" section="Номенклатура" description="Справочник сортов муки.">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
          <StatCard label="Всего сортов" value={String(list.length)} />
          <StatCard label="Активных" value={String(activeN)} accent />
        </div>
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить сорт
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead><TR>
              <SortableHeader label="Сорт" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
              <TH>Статус</TH><TH></TH>
            </TR></THead>
            <TBody>
              {sorted.map((g) => (
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
              {sorted.length === 0 && (
                <TR><TD colSpan={3} className="py-4 text-center text-[var(--muted-foreground)]">
                  Сортов пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый сорт">
        <form onSubmit={add} className="flex flex-col gap-5">
          <div className="grid gap-2">
            <Label>Название сорта</Label>
            <Input placeholder="напр. Премиум" value={name} autoFocus
              onChange={(e) => setName(e.target.value)} required />
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
