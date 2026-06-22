"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { Plus } from "lucide-react";
import type { Employee, Role } from "@/lib/types";

export default function EmployeesPage() {
  const { data: employees, reload } = useApi<Employee[]>("/employees/");
  const { data: roles } = useApi<Role[]>("/roles/");
  const { me } = useAuth();
  const canManage = can(me, "employees.manage");
  const empty = { username: "", password: "", first_name: "", last_name: "", phone: "", position: "", role: "" };
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/employees/", { ...form, role: form.role ? Number(form.role) : null });
      setForm(empty); setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Сотрудники" section="Управление" description="Учётные записи сотрудников и их роли. Создавайте аккаунты и назначайте доступ.">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{employees?.length ?? 0} сотрудников</p>
        {canManage && <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
          <Plus className="size-4" /> Добавить сотрудника</Button>}
      </div>
      <Card><CardContent className="pt-6">
        <Table>
          <THead><TR><TH>Имя</TH><TH>Логин</TH><TH>Должность</TH><TH>Роль</TH><TH>Статус</TH></TR></THead>
          <TBody>
            {(employees ?? []).map((e) => (
              <TR key={e.id}>
                <TD className="font-medium">{e.name}</TD>
                <TD>{e.username}</TD>
                <TD>{e.position || "—"}</TD>
                <TD>{e.role_name || "—"}</TD>
                <TD><Badge tone={e.is_active ? "success" : "muted"}>{e.is_active ? "Активен" : "Отключён"}</Badge></TD>
              </TR>
            ))}
            {(employees ?? []).length === 0 && (
              <TR><TD colSpan={5} className="py-4 text-center text-[var(--muted-foreground)]">Сотрудников пока нет.</TD></TR>)}
          </TBody>
        </Table>
      </CardContent></Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый сотрудник" className="max-w-xl">
        <form onSubmit={submit} className="grid grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
          <div className="grid gap-2"><Label>Имя</Label>
            <Input value={form.first_name} required onChange={(e) => setForm({ ...form, first_name: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Фамилия</Label>
            <Input value={form.last_name} required onChange={(e) => setForm({ ...form, last_name: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Логин</Label>
            <Input value={form.username} required onChange={(e) => setForm({ ...form, username: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Пароль</Label>
            <Input type="password" value={form.password} required minLength={6}
              onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Телефон</Label>
            <Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></div>
          <div className="grid gap-2"><Label>Должность</Label>
            <Input value={form.position} onChange={(e) => setForm({ ...form, position: e.target.value })} /></div>
          <div className="grid gap-2 sm:col-span-2"><Label>Роль</Label>
            <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="">Без роли</option>
              {(roles ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </Select></div>
          {error && <p className="text-sm text-[var(--destructive)] sm:col-span-2">{error}</p>}
          <div className="flex justify-end gap-2 border-t pt-5 sm:col-span-2">
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" disabled={busy}>{busy ? "Сохранение…" : "Создать"}</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
