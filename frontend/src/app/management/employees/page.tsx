"use client";

import { useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BriefcaseBusiness,
  Check,
  KeyRound,
  Pencil,
  Plus,
  Search,
  Trash2,
  UserRound,
} from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { PermissionPicker } from "@/components/permission-picker";
import { RequirePerm } from "@/components/require-perm";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ErrorAlert } from "@/components/ui/data-state";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { PasswordInput } from "@/components/ui/password-input";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { StatCard } from "@/components/ui/stat-card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import type { Department, Employee, Permission } from "@/lib/types";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";
import { useAuth } from "@/store/auth";

const emptyForm = {
  username: "",
  password: "",
  first_name: "",
  last_name: "",
  phone: "",
  position: "",
  sales_department: "",
  is_active: true,
};

function EmployeesPageInner() {
  const { me, refreshMe } = useAuth();
  const canManage = can(me, "employees.manage");
  const canManageSecurity = canManage && can(me, "sys_permissions.manage");
  const canCreateOrDelete = canManageSecurity;

  const { data: employees, error: loadError, reload } = useApi<Employee[]>("/employees/");
  const { data: permissions } = useApi<Permission[]>(canManage ? "/permissions/" : null);

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [selectedPermissions, setSelectedPermissions] = useState<Set<string>>(new Set());
  const [salesEmployee, setSalesEmployee] = useState(false);
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [deleteEmployee, setDeleteEmployee] = useState<Employee | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  const canEditSecurity =
    canManageSecurity &&
    (!editing ||
      (editing.username !== me?.username &&
        (Boolean(me?.is_superuser) || editing.permissions.every((code) => me?.permissions.includes(code)))));
  const canChangePassword =
    canManageSecurity &&
    (!editing ||
      editing.username === me?.username ||
      Boolean(me?.is_superuser) ||
      editing.permissions.every((code) => me?.permissions.includes(code)));
  const canEditDepartment = editing ? canEditSecurity : canCreateOrDelete;
  const ungrantablePermissions = new Set(
    (permissions ?? [])
      .filter(
        (permission) =>
          !me?.is_superuser && !me?.permissions.includes(permission.code) && !selectedPermissions.has(permission.code),
      )
      .map((permission) => permission.code),
  );

  const { data: departments } = useApi<Department[]>(open && canManage ? "/departments/?all=1" : null);

  function openNew() {
    if (!canCreateOrDelete) return;
    setEditing(null);
    setForm(emptyForm);
    setSelectedPermissions(new Set());
    setSalesEmployee(false);
    setStep(1);
    setError("");
    setOpen(true);
  }

  function openEdit(employee: Employee) {
    setEditing(employee);
    setForm({
      username: employee.username,
      password: "",
      first_name: employee.first_name,
      last_name: employee.last_name,
      phone: employee.phone,
      position: employee.position,
      sales_department: employee.sales_department ? String(employee.sales_department) : "",
      is_active: employee.is_active,
    });
    setSelectedPermissions(new Set(employee.permissions));
    setSalesEmployee(Boolean(employee.sales_department));
    setStep(1);
    setError("");
    setOpen(true);
  }

  function togglePermission(code: string) {
    const next = new Set(selectedPermissions);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    setSelectedPermissions(next);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");

    if (step === 1) {
      if (!form.first_name.trim() || !form.last_name.trim() || !form.username.trim()) {
        setError("Заполните имя, фамилию и логин сотрудника.");
        return;
      }
      if (!editing && form.password.length < 8) {
        setError("Пароль должен содержать минимум 8 символов.");
        return;
      }
      setStep(2);
      return;
    }

    if (step === 2) {
      if (salesEmployee && !form.sales_department) {
        setError("Выберите отдел продаж для сотрудника.");
        return;
      }
      setStep(3);
      return;
    }

    setBusy(true);
    try {
      const profile = {
        first_name: form.first_name,
        last_name: form.last_name,
        phone: form.phone,
        position: form.position,
      };
      const salesDepartment = salesEmployee ? Number(form.sales_department) : null;

      if (editing) {
        await api.patch(`/employees/${editing.id}/`, profile);
        if (canEditSecurity) {
          await api.patch(`/employees/${editing.id}/security/`, {
            username: form.username,
            permission_codes: Array.from(selectedPermissions),
            is_active: form.is_active,
            sales_department: salesDepartment,
          });
        }
        if (form.password && canChangePassword) {
          await api.post(`/employees/${editing.id}/password/`, {
            password: form.password,
          });
        }
      } else {
        await api.post("/employees/", {
          ...profile,
          sales_department: salesDepartment,
          username: form.username,
          password: form.password,
          permission_codes: Array.from(selectedPermissions),
          is_active: form.is_active,
        });
      }

      setOpen(false);
      setForm(emptyForm);
      reload();
      refreshMe(true);
    } catch (caught) {
      setError(apiError(caught));
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!deleteEmployee || !canCreateOrDelete) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await api.delete(`/employees/${deleteEmployee.id}/`);
      setDeleteEmployee(null);
      reload();
    } catch (caught) {
      setDeleteError(apiError(caught));
    } finally {
      setDeleteBusy(false);
    }
  }

  const list = employees ?? [];
  const normalizedQuery = query.trim().toLowerCase();
  const filtered = list.filter((employee) => {
    if (!normalizedQuery) return true;
    return `${employee.name} ${employee.username} ${employee.position}`.toLowerCase().includes(normalizedQuery);
  });
  const sorted = [...filtered].sort((left, right) => {
    const result = left.name.localeCompare(right.name, "ru");
    return sortDir === "asc" ? result : -result;
  });

  return (
    <AppShell
      title="Сотрудники"
      section="Управление"
      description="Учётные записи, должности, отделы и персональные системные права."
      actions={
        canCreateOrDelete ? (
          <Button size="sm" onClick={openNew} aria-label="Добавить сотрудника">
            <Plus className="size-4" />
            <span className="hidden sm:inline">Добавить сотрудника</span>
          </Button>
        ) : undefined
      }
    >
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <StatCard label="Всего сотрудников" value={String(list.length)} />
        <StatCard label="Активных" value={String(list.filter((employee) => employee.is_active).length)} accent />
      </section>

      <div className="mb-4">
        <div className="relative max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input
            className="pl-9"
            placeholder="Поиск по имени, логину, должности"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
      </div>

      {loadError && !employees && (
        <div className="mb-4">
          <ErrorAlert message={loadError} onRetry={reload} />
        </div>
      )}

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <SortableHeader
                  label="Имя"
                  sortKey="name"
                  activeKey="name"
                  dir={sortDir}
                  onClick={() => setSortDir(sortDir === "asc" ? "desc" : "asc")}
                />
                <TH>Логин</TH>
                <TH>Должность и отдел</TH>
                <TH>Доступы</TH>
                <TH>Статус</TH>
                <TH />
              </TR>
            </THead>
            <TBody>
              {sorted.map((employee) => (
                <TR key={employee.id}>
                  <TD className="font-medium">{employee.name}</TD>
                  <TD>{employee.username}</TD>
                  <TD>
                    <div>{employee.position || "—"}</div>
                    {employee.sales_department && (
                      <div className="mt-1 inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700">
                        <span
                          className="size-1.5 rounded-full"
                          style={{ backgroundColor: employee.sales_department_color || "#315FD5" }}
                        />
                        {employee.sales_department_name}
                      </div>
                    )}
                  </TD>
                  <TD>{employee.permissions.length}</TD>
                  <TD>
                    <Badge tone={employee.is_active ? "success" : "muted"}>
                      {employee.is_active ? "Активен" : "Отключён"}
                    </Badge>
                  </TD>
                  <TD>
                    {canManage && (
                      <div className="flex items-center justify-end gap-1">
                        <Button size="sm" variant="ghost" onClick={() => openEdit(employee)} title="Изменить">
                          <Pencil className="size-4" />
                        </Button>
                        {canCreateOrDelete && (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                            onClick={() => {
                              setDeleteError("");
                              setDeleteEmployee(employee);
                            }}
                            title="Удалить"
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        )}
                      </div>
                    )}
                  </TD>
                </TR>
              ))}
              {sorted.length === 0 && (
                <TR>
                  <TD colSpan={6} className="py-4 text-center text-[var(--muted-foreground)]">
                    Сотрудников пока нет.
                  </TD>
                </TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow={editing ? "Команда · Изменение" : "Команда · Сотрудник"}
        title={editing ? "Изменить сотрудника" : "Новый сотрудник"}
        description="Данные сотрудника, отдел и персональные доступы."
        className="max-w-2xl"
        mobileFullscreen
        footer={
          <>
            {step === 1 ? (
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Отмена
              </Button>
            ) : (
              <Button type="button" variant="outline" onClick={() => setStep((step - 1) as 1 | 2)}>
                <ArrowLeft className="size-4" /> Назад
              </Button>
            )}
            <Button type="submit" form="employee-form" disabled={busy}>
              {busy ? (
                "Сохранение…"
              ) : step < 3 ? (
                <>
                  Далее <ArrowRight className="size-4" />
                </>
              ) : editing ? (
                "Сохранить"
              ) : (
                "Создать"
              )}
            </Button>
          </>
        }
      >
        <form id="employee-form" onSubmit={submit} className="flex flex-col gap-5">
          <div className="relative grid grid-cols-3 gap-2 rounded-2xl border bg-[var(--muted)]/45 p-2">
            {[
              { number: 1, label: "Сотрудник", icon: UserRound },
              { number: 2, label: "Отдел", icon: BriefcaseBusiness },
              { number: 3, label: "Доступы", icon: KeyRound },
            ].map((item) => {
              const Icon = item.icon;
              const active = step === item.number;
              const done = step > item.number;
              return (
                <button
                  key={item.number}
                  type="button"
                  onClick={() => done && setStep(item.number as 1 | 2 | 3)}
                  className={cn(
                    "relative flex min-w-0 items-center justify-center gap-2 rounded-xl px-2 py-2 text-xs font-semibold transition sm:justify-start",
                    active && "bg-[var(--card)] shadow-sm ring-1 ring-[var(--border)]",
                    done && "text-[var(--success)]",
                  )}
                >
                  <span
                    className={cn(
                      "flex size-8 shrink-0 items-center justify-center rounded-full border bg-[var(--card)]",
                      active && "border-[var(--foreground)] bg-[var(--foreground)] text-[var(--background)]",
                      done && "border-[var(--success)] bg-[var(--success)] text-white",
                    )}
                  >
                    {done ? <Check className="size-4" /> : <Icon className="size-4" />}
                  </span>
                  <span className="hidden truncate sm:block">{item.label}</span>
                </button>
              );
            })}
          </div>

          {step === 1 && (
            <>
              <section className="space-y-3">
                <h4 className="text-xs font-medium text-[var(--muted-foreground)]">Учётная запись</h4>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Field label="Имя" htmlFor="employee-first-name">
                    <Input
                      id="employee-first-name"
                      value={form.first_name}
                      required
                      onChange={(event) => setForm({ ...form, first_name: event.target.value })}
                    />
                  </Field>
                  <Field label="Фамилия" htmlFor="employee-last-name">
                    <Input
                      id="employee-last-name"
                      value={form.last_name}
                      required
                      onChange={(event) => setForm({ ...form, last_name: event.target.value })}
                    />
                  </Field>
                  <Field label="Логин" htmlFor="employee-username">
                    <Input
                      id="employee-username"
                      value={form.username}
                      required
                      disabled={Boolean(editing) && !canEditSecurity}
                      autoComplete="username"
                      onChange={(event) => setForm({ ...form, username: event.target.value })}
                    />
                  </Field>
                  <Field
                    label="Новый пароль"
                    htmlFor="employee-password"
                    hint={editing ? "Оставьте пустым, чтобы не менять." : undefined}
                  >
                    <PasswordInput
                      id="employee-password"
                      value={form.password}
                      required={!editing}
                      minLength={8}
                      disabled={Boolean(editing) && !canChangePassword}
                      autoComplete="new-password"
                      placeholder={editing ? "••••••••" : ""}
                      onChange={(event) => setForm({ ...form, password: event.target.value })}
                    />
                  </Field>
                  <Field label="Телефон" htmlFor="employee-phone">
                    <Input
                      id="employee-phone"
                      type="tel"
                      value={form.phone}
                      onChange={(event) => setForm({ ...form, phone: event.target.value })}
                    />
                  </Field>
                  <Field label="Должность" htmlFor="employee-position">
                    <Input
                      id="employee-position"
                      value={form.position}
                      onChange={(event) => setForm({ ...form, position: event.target.value })}
                    />
                  </Field>
                </div>
              </section>

              {canEditSecurity && (
                <label className="flex items-center gap-3 rounded-xl border p-3 text-sm">
                  <input
                    type="checkbox"
                    checked={form.is_active}
                    onChange={(event) => setForm({ ...form, is_active: event.target.checked })}
                  />
                  Учётная запись активна
                </label>
              )}
            </>
          )}

          {step === 2 && (
            <section className="space-y-4">
              <label className="flex cursor-pointer items-start gap-3 rounded-2xl border border-blue-100 bg-blue-50/60 p-4">
                <input
                  type="checkbox"
                  checked={salesEmployee}
                  disabled={!canEditDepartment}
                  onChange={(event) => {
                    const checked = event.target.checked;
                    setSalesEmployee(checked);
                    if (!checked) {
                      setForm((current) => ({ ...current, sales_department: "" }));
                    } else if (!form.sales_department) {
                      const firstActive = (departments ?? []).find((department) => department.is_active);
                      setForm((current) => ({
                        ...current,
                        sales_department: firstActive ? String(firstActive.id) : "",
                      }));
                    }
                  }}
                />
                <span>
                  <span className="block text-sm font-bold">Сотрудник отдела продаж</span>
                  <span className="mt-1 block text-xs text-[var(--muted-foreground)]">
                    Отдел определяет принадлежность заказов и не выдаёт права автоматически.
                  </span>
                </span>
              </label>

              {salesEmployee && (
                <div className="grid gap-2 sm:grid-cols-2">
                  {(departments ?? []).map((department) => {
                    const selected = form.sales_department === String(department.id);
                    return (
                      <button
                        key={department.id}
                        type="button"
                        disabled={!department.is_active || !canEditDepartment}
                        onClick={() =>
                          setForm((current) => ({
                            ...current,
                            sales_department: String(department.id),
                          }))
                        }
                        className={`flex min-h-12 items-center gap-2.5 rounded-xl border px-3 py-2 text-left text-sm font-semibold transition ${
                          selected
                            ? "border-slate-800 bg-slate-900 text-white"
                            : "border-slate-200 bg-white text-slate-700 disabled:opacity-45"
                        }`}
                      >
                        <span
                          className="size-2.5 shrink-0 rounded-full"
                          style={{ backgroundColor: department.color }}
                        />
                        <span className="truncate">{department.name}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </section>
          )}

          {step === 3 && (
            <section className="space-y-3 border-t border-[var(--border)] pt-4">
              <div className="flex items-center justify-between gap-3">
                <h4 className="text-sm font-medium">Персональные системные права</h4>
                <span className="text-xs text-[var(--muted-foreground)]">Выбрано: {selectedPermissions.size}</span>
              </div>
              <fieldset disabled={!canEditSecurity}>
                <PermissionPicker
                  perms={permissions ?? []}
                  selected={selectedPermissions}
                  onToggle={togglePermission}
                  disabled={ungrantablePermissions}
                />
              </fieldset>
              {!canEditSecurity && (
                <p className="text-xs text-[var(--muted-foreground)]">
                  {canManageSecurity
                    ? "Нельзя изменять собственные доступы или учётную запись с более широкими правами."
                    : "Для изменения доступов требуется право управления системными правами."}
                </p>
              )}
            </section>
          )}

          {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
        </form>
      </Modal>

      <ConfirmDialog
        open={Boolean(deleteEmployee)}
        onClose={() => setDeleteEmployee(null)}
        title="Удалить сотрудника?"
        description={
          deleteEmployee
            ? `Профиль «${deleteEmployee.name}» будет удалён, а учётная запись ${deleteEmployee.username} — отключена.`
            : ""
        }
        busy={deleteBusy}
        error={deleteError}
        onConfirm={confirmDelete}
      />
    </AppShell>
  );
}

export default function EmployeesPage() {
  return (
    <RequirePerm perm="employees.view" title="Сотрудники">
      <EmployeesPageInner />
    </RequirePerm>
  );
}
