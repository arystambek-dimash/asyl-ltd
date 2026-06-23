"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { Field } from "@/components/ui/field";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { Plus, Search, Pencil, Trash2 } from "lucide-react";
import type { Employee, Role } from "@/lib/types";

export default function EmployeesPage() {
  const { data: employees, reload } = useApi<Employee[]>("/employees/");
  const { data: roles } = useApi<Role[]>("/roles/");
  const { me } = useAuth();
  const canManage = can(me, "employees.manage");
  const empty = { username: "", password: "", first_name: "", last_name: "", phone: "", position: "", role: "" };
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [form, setForm] = useState(empty);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [delItem, setDelItem] = useState<Employee | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);

  function openNew() { setEditing(null); setForm(empty); setError(""); setOpen(true); }
  function openEdit(e: Employee) {
    setEditing(e);
    setForm({
      username: e.username, password: "", first_name: e.first_name, last_name: e.last_name,
      phone: e.phone, position: e.position, role: e.role ? String(e.role) : "",
    });
    setError(""); setOpen(true);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const role = form.role ? Number(form.role) : null;
      if (editing) {
        const body: Record<string, unknown> = {
          username: form.username, first_name: form.first_name, last_name: form.last_name,
          phone: form.phone, position: form.position, role,
        };
        if (form.password) body.password = form.password;  // пустой = не менять
        await api.patch(`/employees/${editing.id}/`, body);
      } else {
        await api.post("/employees/", { ...form, role });
      }
      setForm(empty); setOpen(false); reload();
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
        <Button size="sm" onClick={openNew}>
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
                <TD>{e.role_name || "—"}</TD>
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
        description="Создайте аккаунт коллеге и выдайте доступ."
        className="max-w-xl"
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
                <Input type="password" value={form.password} required={!editing} minLength={6}
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
            <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Роль</h4>
            <Field label="Роль">
              <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="">Без роли</option>
                {(roles ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </Select>
            </Field>
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
