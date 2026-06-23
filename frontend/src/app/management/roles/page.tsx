"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { Plus, Trash2 } from "lucide-react";
import type { Role, Permission } from "@/lib/types";

const SECTION_LABELS: Record<string, string> = {
  catalog: "Номенклатура", clients: "Клиенты", warehouse: "Склад", orders: "Заказы",
  payments: "Оплаты", shipping: "Пост отгрузки", events: "Журнал", reports: "Отчёты",
  employees: "Сотрудники",
};
const ACTION_LABELS: Record<string, string> = {
  view: "просмотр", create: "создание", edit: "редакт.", delete: "удаление",
  adjust: "корректировка", confirm: "подтвержд.", arrive: "приём", load: "загрузка",
  ship: "отгрузка", debt_override: "в долг", manage: "управление",
};

export default function RolesPage() {
  const { data: roles, reload } = useApi<Role[]>("/roles/");
  const { data: perms } = useApi<Permission[]>("/permissions/");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);
  const [name, setName] = useState("");
  const [codes, setCodes] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const sections = Array.from(new Set((perms ?? []).map((p) => p.section)));

  function openNew() { setEditing(null); setName(""); setCodes(new Set()); setError(""); setOpen(true); }
  function openEdit(r: Role) {
    setEditing(r); setName(r.name);
    setCodes(new Set(r.permissions.map((p) => p.code))); setError(""); setOpen(true);
  }
  function toggle(code: string) {
    const next = new Set(codes);
    if (next.has(code)) next.delete(code); else next.add(code);
    setCodes(next);
  }
  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    const body = { name, permission_codes: Array.from(codes) };
    try {
      if (editing) await api.patch(`/roles/${editing.id}/`, body);
      else await api.post("/roles/", body);
      setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }
  async function remove(r: Role) {
    setError("");
    try { await api.delete(`/roles/${r.id}/`); reload(); }
    catch (e) { setError(apiError(e)); }
  }

  return (
    <AppShell title="Роли" section="Управление" description="Гибкие роли с настраиваемыми правами по разделам и действиям."
      actions={
        <Button size="sm" onClick={openNew}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Новая роль</span>
        </Button>
      }>
      <div className="mb-4">
        <p className="text-sm text-[var(--muted-foreground)]">{roles?.length ?? 0} ролей</p>
      </div>
      {error && <p className="mb-3 text-sm text-[var(--destructive)]">{error}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(roles ?? []).map((r) => (
          <Card key={r.id}>
            <CardHeader className="flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">{r.name}
                {r.is_system && <Badge tone="muted">системная</Badge>}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <p className="text-xs text-[var(--muted-foreground)]">
                Прав: {r.permissions.length} · Сотрудников: {r.employee_count}</p>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => openEdit(r)}>Изменить</Button>
                {!r.is_system && r.employee_count === 0 && (
                  <Button size="sm" variant="ghost" onClick={() => remove(r)}>
                    <Trash2 className="size-4" /></Button>)}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Доступы · Роль"
        title={editing ? `Роль: ${editing.name}` : "Новая роль"}
        description="Название и набор прав по разделам."
        className="max-w-2xl">
        <form onSubmit={save} className="flex flex-col gap-5">
          <div className="grid gap-2"><Label>Название роли</Label>
            <Input value={name} required onChange={(e) => setName(e.target.value)}
              disabled={editing?.is_system} /></div>
          <div className="flex flex-col gap-4">
            <Label>Права доступа</Label>
            {sections.map((sec) => (
              <div key={sec} className="rounded-lg border p-3">
                <div className="mb-2 text-sm font-semibold">{SECTION_LABELS[sec] ?? sec}</div>
                <div className="flex flex-wrap gap-2">
                  {(perms ?? []).filter((p) => p.section === sec).map((p) => (
                    <button key={p.code} type="button" onClick={() => toggle(p.code)}
                      className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                        codes.has(p.code)
                          ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                          : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]"}`}>
                      {ACTION_LABELS[p.action] ?? p.action}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div className="flex justify-end gap-2 border-t pt-5">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
