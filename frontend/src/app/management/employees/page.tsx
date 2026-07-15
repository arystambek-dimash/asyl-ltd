"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { Field } from "@/components/ui/field";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorAlert } from "@/components/ui/data-state";
import { PermissionPicker } from "@/components/permission-picker";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { Plus, Search, Pencil, Trash2 } from "lucide-react";
import type { Employee, Permission, Role } from "@/lib/types";

function EmployeesPageInner() {
  const { data: employees, error: loadError, reload } = useApi<Employee[]>("/employees/");
  const { data: roles } = useApi<Role[]>("/roles/");
  const { data: perms } = useApi<Permission[]>("/permissions/");
  const { me, refreshMe } = useAuth();
  const canManage = can(me, "employees.manage");
  const empty = { username: "", password: "", first_name: "", last_name: "", phone: "", position: "", role: "" };
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [form, setForm] = useState(empty);
  // Личные доступы поверх роли; права самой роли наследуются автоматически.
  const [codes, setCodes] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [delItem, setDelItem] = useState<Employee | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);

  function openNew() { setEditing(null); setForm(empty); setCodes(new Set()); setError(""); setOpen(true); }
  function openEdit(e: Employee) {
    setEditing(e);
    setForm({
      username: e.username, password: "", first_name: e.first_name, last_name: e.last_name,
      phone: e.phone, position: e.position, role: e.role ? String(e.role) : "",
    });
    const inRole = new Set(e.role_permissions ?? []);
    setCodes(new Set((e.permissions ?? []).filter((c) => !inRole.has(c))));
    setError(""); setOpen(true);
  }

  function toggleCode(code: string) {
    const next = new Set(codes);
    if (next.has(code)) next.delete(code); else next.add(code);
    setCodes(next);
  }

  // Права выбранной роли действуют сами — из личного набора убираем дубли.
  const rolePerms = new Set(
    ((roles ?? []).find((r) => String(r.id) === form.role)?.permissions ?? []).map((p) => p.code));
  function pickRole(roleId: string) {
    setForm((f) => ({ ...f, role: roleId }));
    const preset = (roles ?? []).find((r) => String(r.id) === roleId);
    const inRole = new Set((preset?.permissions ?? []).map((p) => p.code));
    setCodes((prev) => new Set([...prev].filter((c) => !inRole.has(c))));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const role = form.role ? Number(form.role) : null;
      const body: Record<string, unknown> = {
        username: form.username, first_name: form.first_name, last_name: form.last_name,
        phone: form.phone, position: form.position, role,
        permission_codes: Array.from(codes),
      };
      if (editing) {
        if (form.password) body.password = form.password;  // пустой = не менять
        await api.patch(`/employees/${editing.id}/`, body);
      } else {
        await api.post("/employees/", { ...body, password: form.password });
      }
      setForm(empty); setOpen(false); reload();
      refreshMe(true); // если админ менял свои же доступы — применить сразу
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true); setDelError("");
    try {
      await api.delete(`/employees/${delItem.id}/`);
      setDelItem(null); reload();
    } catch (e) { setDelError(apiError(e)); } finally { setDelBusy(false); }
  }

  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = employees ?? [];
  const activeN = list.filter((e) => e.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const filtered = list.filter((e) => {
    if (!q) return true;
    return `${e.name} ${e.username} ${e.position ?? ""}`.toLowerCase().includes(q.toLowerCase());
  });
  const sorted = [...filtered].sort((a, b) => {
    const av = sortKey === "role" ? (a.role_name ?? "") : a.name;
    const bv = sortKey === "role" ? (b.role_name ?? "") : b.name;
    const cmp = String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Сотрудники" section="Управление" description="Учётные записи сотрудников и их роли. Создавайте аккаунты и назначайте доступ."
      actions={canManage ? (
        <Button size="sm" onClick={openNew} aria-label="Добавить сотрудника">
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить сотрудника</span>
        </Button>
      ) : undefined}>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <StatCard label="Всего сотрудников" value={String(list.length)} />
        <StatCard label="Активных" value={String(activeN)} accent />
      </section>
      <div className="mb-4">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по имени, логину, должности"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      {loadError && !employees && <div className="mb-4"><ErrorAlert message={loadError} onRetry={reload} /></div>}
      <Card><CardContent className="pt-6">
        <Table>
          <THead><TR>
            <SortableHeader label="Имя" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
            <TH>Логин</TH><TH>Должность</TH>
            <SortableHeader label="Роль" sortKey="role" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
            <TH>Статус</TH><TH></TH>
          </TR></THead>
          <TBody>
            {sorted.map((e) => (
              <TR key={e.id}>
                <TD className="font-medium">{e.name}</TD>
                <TD>{e.username}</TD>
                <TD>{e.position || "—"}</TD>
                <TD>
                  <div>{e.role_name || "—"}</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    Доступов: {new Set([...(e.role_permissions ?? []), ...(e.permissions ?? [])]).size}
                  </div>
                </TD>
                <TD><Badge tone={e.is_active ? "success" : "muted"}>{e.is_active ? "Активен" : "Отключён"}</Badge></TD>
                <TD>
                  {canManage && (
                    <div className="flex items-center justify-end gap-1">
                      <Button size="sm" variant="ghost" onClick={() => openEdit(e)} title="Изменить">
                        <Pencil className="size-4" />
                      </Button>
                      <Button size="sm" variant="ghost"
                        className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                        onClick={() => { setDelError(""); setDelItem(e); }} title="Удалить">
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  )}
                </TD>
              </TR>
            ))}
            {sorted.length === 0 && (
              <TR><TD colSpan={6} className="py-4 text-center text-[var(--muted-foreground)]">Сотрудников пока нет.</TD></TR>)}
          </TBody>
        </Table>
      </CardContent></Card>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow={editing ? "Команда · Изменение" : "Команда · Сотрудник"}
        title={editing ? "Изменить сотрудника" : "Новый сотрудник"}
        description="Создайте аккаунт коллеге и выберите, к чему он имеет доступ."
        className="max-w-2xl"
        footer={
          <>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" form="employee-form" disabled={busy}>
              {busy ? "Сохранение…" : editing ? "Сохранить" : "Создать"}</Button>
          </>
        }>
        <form id="employee-form" onSubmit={submit} className="flex flex-col gap-5">
          <section className="space-y-3">
            <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Человек</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Имя">
                <Input value={form.first_name} required onChange={(e) => setForm({ ...form, first_name: e.target.value })} />
              </Field>
              <Field label="Фамилия">
                <Input value={form.last_name} required onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
              </Field>
              <Field label="Логин">
                <Input value={form.username} required onChange={(e) => setForm({ ...form, username: e.target.value })} />
              </Field>
              <Field label="Пароль" hint={editing ? "Оставьте пустым, чтобы не менять." : undefined}>
                <PasswordInput value={form.password} required={!editing} minLength={6}
                  placeholder={editing ? "••••••" : ""}
                  onChange={(e) => setForm({ ...form, password: e.target.value })} />
              </Field>
            </div>
          </section>

          <section className="space-y-3 border-t border-[var(--border)] pt-4">
            <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Должность</h4>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Телефон">
                <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
              </Field>
              <Field label="Должность">
                <Input value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} />
              </Field>
            </div>
          </section>

          <section className="space-y-3 border-t border-[var(--border)] pt-4">
            <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Доступы</h4>
            <Field label="Роль"
              hint="Роль сразу даёт свои доступы. Поменяете права роли — изменится доступ у всех сотрудников с ней.">
              <Select value={form.role} onChange={(e) => pickRole(e.target.value)}>
                <option value="">Без роли</option>
                {(roles ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </Select>
            </Field>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">К чему имеет доступ</span>
                <span className="text-xs text-[var(--muted-foreground)]">
                  {rolePerms.size > 0 ? `Из роли: ${rolePerms.size} · ` : ""}Личных: {codes.size}
                </span>
              </div>
              <PermissionPicker perms={perms ?? []} selected={codes} onToggle={toggleCode}
                inherited={rolePerms} />
            </div>
          </section>

          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
        </form>
      </Modal>

      <ConfirmDialog
        open={!!delItem}
        onClose={() => setDelItem(null)}
        title="Удалить сотрудника?"
        description={delItem ? `Аккаунт «${delItem.name}» (${delItem.username}) будет удалён.` : ""}
        busy={delBusy}
        error={delError}
        onConfirm={confirmDelete}
      />
    </AppShell>
  );
}

export default function EmployeesPage() {
  return <RequirePerm perm="employees.view" title="Сотрудники"><EmployeesPageInner /></RequirePerm>;
}
