"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { api, apiError } from "@/lib/api";
import { Plus, Trash2 } from "lucide-react";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorAlert } from "@/components/ui/data-state";
import { PermissionPicker } from "@/components/permission-picker";
import type { Role, Permission } from "@/lib/types";

function RolesPageInner() {
  const { me, refreshMe } = useAuth();
  const canManage = can(me, "rbac.manage");
  const { data: roles, error: loadError, reload } = useApi<Role[]>("/roles/");
  const { data: perms } = useApi<Permission[]>("/permissions/");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Role | null>(null);
  const [name, setName] = useState("");
  const [codes, setCodes] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [delRole, setDelRole] = useState<Role | null>(null);
  const [delBusy, setDelBusy] = useState(false);
  const [delError, setDelError] = useState("");

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
      refreshMe(true); // права роли могли коснуться и текущего пользователя
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }
  async function confirmRemove() {
    if (!delRole) return;
    setDelBusy(true); setDelError("");
    try { await api.delete(`/roles/${delRole.id}/`); setDelRole(null); reload(); }
    catch (e) { setDelError(apiError(e)); } finally { setDelBusy(false); }
  }

  return (
    <AppShell title="Роли" section="Управление"
      description="Роль определяет доступы сотрудников. Изменили права роли — они сразу действуют у всех, кому она назначена."
      actions={canManage && (
        <Button size="sm" onClick={openNew} aria-label="Новая роль">
          <Plus className="size-4" /> <span className="hidden sm:inline">Новая роль</span>
        </Button>
      )}>
      <div className="mb-4">
        <p className="text-sm text-[var(--muted-foreground)]">{roles?.length ?? 0} ролей</p>
      </div>
      {error && <p className="mb-3 text-sm text-[var(--destructive)]">{error}</p>}
      {loadError && !roles && <div className="mb-3"><ErrorAlert message={loadError} onRetry={reload} /></div>}
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
                <Button size="sm" variant="outline" onClick={() => openEdit(r)}>
                  {canManage ? "Изменить" : "Посмотреть"}
                </Button>
                {canManage && r.employee_count === 0 && (
                  <Button size="sm" variant="ghost"
                    className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                    onClick={() => { setDelError(""); setDelRole(r); }} title="Удалить">
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
            <Label>Права роли — действуют на всех сотрудников с этой ролью</Label>
            <PermissionPicker perms={perms ?? []} selected={codes} onToggle={toggle} />
          </div>
          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
          <div className="flex justify-end gap-2 border-t pt-5">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              {canManage ? "Отмена" : "Закрыть"}
            </Button>
            {canManage && (
              <Button type="submit" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</Button>
            )}
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={!!delRole}
        onClose={() => setDelRole(null)}
        title="Удалить роль?"
        description={delRole
          ? `Роль «${delRole.name}»${delRole.is_system ? " (системная)" : ""} будет удалена. Действие необратимо.`
          : ""}
        busy={delBusy}
        error={delError}
        onConfirm={confirmRemove}
      />
    </AppShell>
  );
}

export default function RolesPage() {
  return <RequirePerm perm="rbac.view" title="Роли"><RolesPageInner /></RequirePerm>;
}
