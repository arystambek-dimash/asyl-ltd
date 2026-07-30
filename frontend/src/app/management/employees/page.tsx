"use client";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
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
import { RolesSection } from "@/components/roles-section";
import { Tabs, type TabDef } from "@/components/ui/tabs";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { cn } from "@/lib/utils";
import {
  ArrowLeft,
  ArrowRight,
  BriefcaseBusiness,
  Check,
  KeyRound,
  Plus,
  Search,
  Pencil,
  ShieldCheck,
  Trash2,
  UserRound,
} from "lucide-react";
import type { Department, Employee, Permission, Role } from "@/lib/types";

const SALES_REQUIRED = new Set(["orders.view", "orders.create", "clients.view", "catalog.view"]);

function effectiveAccessCount(employee: Employee) {
  const denied = new Set(employee.denied_permissions ?? []);
  const effective = new Set([
    ...(employee.role_permissions ?? []).filter((code) => !denied.has(code)),
    ...(employee.permissions ?? []),
  ]);
  if (employee.sales_department) {
    SALES_REQUIRED.forEach((code) => effective.add(code));
  }
  return effective.size;
}

function EmployeesPageInner() {
  const searchParams = useSearchParams();
  const { me: viewer } = useAuth();
  const canStaff = can(viewer, "employees.view");
  const canRoles = can(viewer, "rbac.view");
  // Роли слиты сюда с бывшей страницы /management/roles: раздел «Доступы»
  // из двух пунктов меню стал одним экраном с вкладками.
  const [tab, setTab] = useState<"staff" | "roles">(
    searchParams.get("tab") === "roles" && canRoles ? "roles" : canStaff ? "staff" : "roles",
  );
  const tabs: TabDef[] = [
    ...(canStaff ? [{ key: "staff", label: "Сотрудники" }] : []),
    ...(canRoles ? [{ key: "roles", label: "Роли" }] : []),
  ];
  const { data: employees, error: loadError, reload } = useApi<Employee[]>(canStaff ? "/employees/" : null);
  const { data: roles } = useApi<Role[]>("/roles/");
  const { data: perms } = useApi<Permission[]>("/permissions/");
  const { data: departments } = useApi<Department[]>("/departments/?all=1");
  const { me, refreshMe } = useAuth();
  const canManage = can(me, "employees.manage");
  const empty = {
    username: "",
    password: "",
    first_name: "",
    last_name: "",
    phone: "",
    position: "",
    role: "",
    sales_department: "",
  };
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [form, setForm] = useState(empty);
  const [salesEmployee, setSalesEmployee] = useState(false);
  // Личные доступы поверх роли; права самой роли наследуются автоматически.
  const [codes, setCodes] = useState<Set<string>>(new Set());
  const [deniedCodes, setDeniedCodes] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [employeeStep, setEmployeeStep] = useState<1 | 2 | 3>(1);
  const [delItem, setDelItem] = useState<Employee | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);

  function openNew() {
    setEditing(null);
    setForm(empty);
    setSalesEmployee(false);
    setCodes(new Set());
    setDeniedCodes(new Set());
    setEmployeeStep(1);
    setError("");
    setOpen(true);
  }
  function openEdit(e: Employee) {
    setEditing(e);
    setForm({
      username: e.username,
      password: "",
      first_name: e.first_name,
      last_name: e.last_name,
      phone: e.phone,
      position: e.position,
      role: e.role ? String(e.role) : "",
      sales_department: e.sales_department ? String(e.sales_department) : "",
    });
    setSalesEmployee(!!e.sales_department);
    const inRole = new Set(e.role_permissions ?? []);
    setCodes(new Set((e.permissions ?? []).filter((c) => !inRole.has(c))));
    setDeniedCodes(new Set(e.denied_permissions ?? []));
    setEmployeeStep(1);
    setError("");
    setOpen(true);
  }

  function toggleCode(code: string) {
    const next = new Set(codes);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    setCodes(next);
  }

  function toggleDeniedCode(code: string) {
    if (salesEmployee && SALES_REQUIRED.has(code)) return;
    const next = new Set(deniedCodes);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    setDeniedCodes(next);
  }

  // Права выбранной роли действуют сами — из личного набора убираем дубли.
  const rolePerms = new Set(
    ((roles ?? []).find((r) => String(r.id) === form.role)?.permissions ?? []).map((p) => p.code),
  );
  function pickRole(roleId: string) {
    setForm((f) => ({ ...f, role: roleId }));
    const preset = (roles ?? []).find((r) => String(r.id) === roleId);
    const inRole = new Set((preset?.permissions ?? []).map((p) => p.code));
    setCodes((prev) => new Set([...prev].filter((c) => !inRole.has(c))));
    setDeniedCodes((prev) => new Set([...prev].filter((c) => inRole.has(c))));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (employeeStep === 1) {
      if (!form.first_name.trim() || !form.last_name.trim() || !form.username.trim()) {
        setError("Заполните имя, фамилию и логин сотрудника.");
        return;
      }
      if (!editing && form.password.length < 6) {
        setError("Пароль должен содержать минимум 6 символов.");
        return;
      }
      setEmployeeStep(2);
      return;
    }
    if (employeeStep === 2) {
      if (salesEmployee && !form.sales_department) {
        setError("Выберите отдел продаж для сотрудника.");
        return;
      }
      setEmployeeStep(3);
      return;
    }
    setBusy(true);
    try {
      const role = form.role ? Number(form.role) : null;
      const body: Record<string, unknown> = {
        username: form.username,
        first_name: form.first_name,
        last_name: form.last_name,
        phone: form.phone,
        position: form.position,
        role,
        sales_department: salesEmployee ? Number(form.sales_department) : null,
        permission_codes: Array.from(codes),
        denied_permission_codes: Array.from(deniedCodes),
      };
      if (salesEmployee && !form.sales_department) {
        throw new Error("sales_department_required");
      }
      if (editing) {
        if (form.password) body.password = form.password; // пустой = не менять
        await api.patch(`/employees/${editing.id}/`, body);
      } else {
        await api.post("/employees/", { ...body, password: form.password });
      }
      setForm(empty);
      setOpen(false);
      reload();
      refreshMe(true); // если админ менял свои же доступы — применить сразу
    } catch (e) {
      setError(
        e instanceof Error && e.message === "sales_department_required"
          ? "Выберите отдел продаж для сотрудника."
          : apiError(e),
      );
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true);
    setDelError("");
    try {
      await api.delete(`/employees/${delItem.id}/`);
      setDelItem(null);
      reload();
    } catch (e) {
      setDelError(apiError(e));
    } finally {
      setDelBusy(false);
    }
  }

  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const list = employees ?? [];
  const activeN = list.filter((e) => e.is_active).length;
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(k);
      setSortDir("asc");
    }
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
    <AppShell
      title="Сотрудники"
      section="Управление"
      description="Учётные записи, роли и доступы — всё управление людьми на одном экране."
      actions={
        tab === "staff" && canManage ? (
          <Button size="sm" onClick={openNew} aria-label="Добавить сотрудника">
            <Plus className="size-4" /> <span className="hidden sm:inline">Добавить сотрудника</span>
          </Button>
        ) : undefined
      }
    >
      {tabs.length > 1 && (
        <div className="mb-5">
          <Tabs tabs={tabs} active={tab} onChange={(key) => setTab(key as "staff" | "roles")} />
        </div>
      )}
      {tab === "roles" ? (
        <RolesSection />
      ) : (
        <>
          <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <StatCard label="Всего сотрудников" value={String(list.length)} />
            <StatCard label="Активных" value={String(activeN)} accent />
          </section>
          <div className="mb-4">
            <div className="relative max-w-md flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
              <Input
                className="pl-9"
                placeholder="Поиск по имени, логину, должности"
                aria-label="Поиск сотрудников по имени, логину или должности"
                value={q}
                onChange={(e) => setQ(e.target.value)}
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
              <div className="space-y-3 md:hidden">
                {sorted.map((employee) => (
                  <article
                    key={employee.id}
                    className="rounded-2xl border border-[var(--border)] bg-[var(--card)] p-4 shadow-card"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="font-semibold">{employee.name}</h3>
                          <Badge tone={employee.is_active ? "success" : "muted"}>
                            {employee.is_active ? "Активен" : "Отключён"}
                          </Badge>
                        </div>
                        <p className="mt-1 text-sm text-[var(--muted-foreground)]">@{employee.username}</p>
                      </div>
                      {canManage && (
                        <div className="flex shrink-0 items-center gap-1">
                          <Button
                            size="icon"
                            variant="ghost"
                            onClick={() => openEdit(employee)}
                            aria-label={`Изменить сотрудника ${employee.name}`}
                          >
                            <Pencil className="size-4" />
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                            onClick={() => {
                              setDelError("");
                              setDelItem(employee);
                            }}
                            aria-label={`Удалить сотрудника ${employee.name}`}
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      )}
                    </div>
                    <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-[var(--border)] pt-4 text-sm">
                      <div>
                        <dt className="text-xs text-[var(--muted-foreground)]">Должность</dt>
                        <dd className="mt-1 font-medium">{employee.position || "Не указана"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-[var(--muted-foreground)]">Роль</dt>
                        <dd className="mt-1 font-medium">{employee.role_name || "Без роли"}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-[var(--muted-foreground)]">Доступы</dt>
                        <dd className="mt-1 font-medium tabular-nums">{effectiveAccessCount(employee)}</dd>
                      </div>
                      {employee.sales_department && (
                        <div>
                          <dt className="text-xs text-[var(--muted-foreground)]">Отдел</dt>
                          <dd className="mt-1 flex items-center gap-1.5 font-medium">
                            <span
                              className="size-1.5 rounded-full"
                              style={{ backgroundColor: employee.sales_department_color || "var(--ring)" }}
                            />
                            <span className="truncate">{employee.sales_department_name}</span>
                          </dd>
                        </div>
                      )}
                    </dl>
                  </article>
                ))}
                {sorted.length === 0 && (
                  <div className="rounded-2xl border border-dashed border-[var(--border)] px-4 py-10 text-center text-sm text-[var(--muted-foreground)]">
                    Сотрудников пока нет.
                  </div>
                )}
              </div>

              <div className="hidden md:block">
                <Table>
                  <THead>
                    <TR>
                      <SortableHeader
                        label="Имя"
                        sortKey="name"
                        activeKey={sortKey}
                        dir={sortDir}
                        onClick={toggleSort}
                      />
                      <TH>Логин</TH>
                      <TH>Должность</TH>
                      <SortableHeader
                        label="Роль"
                        sortKey="role"
                        activeKey={sortKey}
                        dir={sortDir}
                        onClick={toggleSort}
                      />
                      <TH>Статус</TH>
                      <TH></TH>
                    </TR>
                  </THead>
                  <TBody>
                    {sorted.map((e) => (
                      <TR key={e.id}>
                        <TD className="font-medium">{e.name}</TD>
                        <TD>{e.username}</TD>
                        <TD>
                          <div>{e.position || "—"}</div>
                          {e.sales_department && (
                            <div className="mt-1 inline-flex items-center gap-1.5 rounded-full bg-[var(--soft-blue)] px-2 py-0.5 text-[11px] font-semibold text-[var(--soft-blue-foreground)]">
                              <span
                                className="size-1.5 rounded-full"
                                style={{ backgroundColor: e.sales_department_color || "var(--ring)" }}
                              />
                              {e.sales_department_name}
                            </div>
                          )}
                        </TD>
                        <TD>
                          <div>{e.role_name || "—"}</div>
                          <div className="text-xs text-[var(--muted-foreground)]">
                            Доступов: {effectiveAccessCount(e)}
                          </div>
                        </TD>
                        <TD>
                          <Badge tone={e.is_active ? "success" : "muted"}>{e.is_active ? "Активен" : "Отключён"}</Badge>
                        </TD>
                        <TD>
                          {canManage && (
                            <div className="flex items-center justify-end gap-1">
                              <Button
                                size="sm"
                                variant="ghost"
                                onClick={() => openEdit(e)}
                                aria-label={`Изменить сотрудника ${e.name}`}
                                title="Изменить"
                              >
                                <Pencil className="size-4" />
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                                onClick={() => {
                                  setDelError("");
                                  setDelItem(e);
                                }}
                                aria-label={`Удалить сотрудника ${e.name}`}
                                title="Удалить"
                              >
                                <Trash2 className="size-4" />
                              </Button>
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
              </div>
            </CardContent>
          </Card>

          <Modal
            open={open}
            onClose={() => setOpen(false)}
            eyebrow={editing ? "Команда · Изменение" : "Команда · Сотрудник"}
            title={editing ? "Изменить сотрудника" : "Новый сотрудник"}
            description="Данные сотрудника, его роль и точные доступы — по шагам."
            className="max-w-2xl"
            mobileFullscreen
            footer={
              <>
                {employeeStep === 1 ? (
                  <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                    Отмена
                  </Button>
                ) : (
                  <Button type="button" variant="outline" onClick={() => setEmployeeStep((employeeStep - 1) as 1 | 2)}>
                    <ArrowLeft className="size-4" /> Назад
                  </Button>
                )}
                <Button type="submit" form="employee-form" disabled={busy}>
                  {busy ? (
                    "Сохранение…"
                  ) : employeeStep < 3 ? (
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
                  { n: 1, label: "Сотрудник", icon: UserRound },
                  { n: 2, label: "Роль и отдел", icon: BriefcaseBusiness },
                  { n: 3, label: "Доступы", icon: KeyRound },
                ].map((item) => {
                  const Icon = item.icon;
                  const active = employeeStep === item.n;
                  const done = employeeStep > item.n;
                  return (
                    <button
                      key={item.n}
                      type="button"
                      onClick={() => done && setEmployeeStep(item.n as 1 | 2 | 3)}
                      aria-current={active ? "step" : undefined}
                      aria-label={`Шаг ${item.n}: ${item.label}${done ? ", завершён" : active ? ", текущий" : ""}`}
                      aria-disabled={!active && !done}
                      className={cn(
                        "relative flex min-w-0 flex-col items-center justify-center gap-1 rounded-xl px-1.5 py-2 text-[10px] font-semibold transition sm:flex-row sm:justify-start sm:gap-2 sm:px-2 sm:text-xs",
                        active && "bg-[var(--card)] shadow-sm ring-1 ring-[var(--border)]",
                        done && "text-[var(--success)]",
                        !active && !done && "cursor-default",
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
                      <span className="max-w-full truncate">{item.label}</span>
                    </button>
                  );
                })}
              </div>

              {employeeStep === 1 && (
                <>
                  <section className="space-y-3">
                    <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Человек</h4>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Field label="Имя" htmlFor="employee-first-name">
                        <Input
                          id="employee-first-name"
                          value={form.first_name}
                          required
                          onChange={(e) => setForm({ ...form, first_name: e.target.value })}
                        />
                      </Field>
                      <Field label="Фамилия" htmlFor="employee-last-name">
                        <Input
                          id="employee-last-name"
                          value={form.last_name}
                          required
                          onChange={(e) => setForm({ ...form, last_name: e.target.value })}
                        />
                      </Field>
                      <Field label="Логин" htmlFor="employee-username">
                        <Input
                          id="employee-username"
                          value={form.username}
                          required
                          autoComplete="username"
                          onChange={(e) => setForm({ ...form, username: e.target.value })}
                        />
                      </Field>
                      <Field
                        label="Пароль"
                        htmlFor="employee-password"
                        hint={editing ? "Оставьте пустым, чтобы не менять." : undefined}
                      >
                        <PasswordInput
                          id="employee-password"
                          value={form.password}
                          required={!editing}
                          minLength={6}
                          autoComplete="new-password"
                          placeholder={editing ? "••••••" : ""}
                          onChange={(e) => setForm({ ...form, password: e.target.value })}
                        />
                      </Field>
                    </div>
                  </section>

                  <section className="space-y-3 border-t border-[var(--border)] pt-4">
                    <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Должность</h4>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <Field label="Телефон" htmlFor="employee-phone">
                        <Input
                          id="employee-phone"
                          type="tel"
                          autoComplete="tel"
                          value={form.phone}
                          onChange={(e) => setForm({ ...form, phone: e.target.value })}
                        />
                      </Field>
                      <Field label="Должность" htmlFor="employee-position">
                        <Input
                          id="employee-position"
                          value={form.position}
                          onChange={(e) => setForm({ ...form, position: e.target.value })}
                        />
                      </Field>
                    </div>
                  </section>
                </>
              )}

              {employeeStep === 2 && (
                <>
                  <section className="space-y-3 border-t border-[var(--border)] pt-4">
                    <label className="group flex min-h-12 cursor-pointer items-start gap-3 rounded-2xl border border-[var(--soft-blue-border)] bg-[var(--soft-blue)] p-4 transition hover:border-[var(--ring)]/35">
                      <input
                        type="checkbox"
                        checked={salesEmployee}
                        onChange={(event) => {
                          const checked = event.target.checked;
                          setSalesEmployee(checked);
                          if (checked && !form.sales_department) {
                            const first = (departments ?? []).find((department) => department.is_active);
                            setForm((current) => ({ ...current, sales_department: first ? String(first.id) : "" }));
                          }
                          if (!checked) setForm((current) => ({ ...current, sales_department: "" }));
                          if (checked) {
                            setDeniedCodes(
                              (current) => new Set([...current].filter((code) => !SALES_REQUIRED.has(code))),
                            );
                          }
                        }}
                        className="peer sr-only"
                      />
                      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-lg border border-[var(--soft-blue-border)] bg-[var(--card)] text-transparent shadow-sm transition peer-checked:border-[var(--ring)] peer-checked:bg-[var(--ring)] peer-checked:text-white">
                        <Check className="size-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center gap-2 text-sm font-bold text-[var(--foreground)]">
                          <BriefcaseBusiness className="size-4 text-[var(--soft-blue-foreground)]" /> Сотрудник отдела
                          продаж
                        </span>
                        <span className="mt-1 block text-xs leading-relaxed text-[var(--muted-foreground)]">
                          Его отдел будет автоматически закрепляться за новыми заказами. Просмотр клиентов, товаров и
                          создание заказов включаются обязательно.
                        </span>
                      </span>
                    </label>

                    {salesEmployee && (
                      <div className="rounded-2xl border border-[var(--border)] bg-[var(--secondary)] p-4">
                        <div className="mb-3 flex items-center justify-between gap-3">
                          <div>
                            <div className="text-sm font-bold text-[var(--foreground)]">Закреплённый отдел</div>
                            <div className="mt-0.5 text-xs text-[var(--muted-foreground)]">
                              Все новые заказы сотрудника попадут сюда.
                            </div>
                          </div>
                          <ShieldCheck className="size-5 text-[var(--soft-green-foreground)]" />
                        </div>
                        <div className="grid gap-2 sm:grid-cols-2">
                          {(departments ?? []).map((department) => {
                            const selected = form.sales_department === String(department.id);
                            return (
                              <button
                                key={department.id}
                                type="button"
                                disabled={!department.is_active}
                                onClick={() =>
                                  setForm((current) => ({ ...current, sales_department: String(department.id) }))
                                }
                                className={`flex min-h-12 items-center gap-2.5 rounded-xl border px-3 py-2 text-left text-sm font-semibold transition ${
                                  selected
                                    ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md"
                                    : "border-[var(--border)] bg-[var(--card)] text-[var(--foreground)] hover:-translate-y-0.5 hover:border-[var(--input)] disabled:cursor-not-allowed disabled:opacity-45"
                                }`}
                              >
                                <span
                                  className="size-2.5 shrink-0 rounded-full ring-4 ring-current/10"
                                  style={{ backgroundColor: department.color, color: department.color }}
                                />
                                <span className="truncate">{department.name}</span>
                                {!department.is_active && (
                                  <span className="ml-auto text-[10px] font-normal">отключён</span>
                                )}
                              </button>
                            );
                          })}
                        </div>
                        {!departments?.some((department) => department.is_active) && (
                          <p className="mt-3 text-xs font-medium text-[var(--destructive)]">
                            Нет действующих отделов. Сначала создайте отдел на странице заказов.
                          </p>
                        )}
                      </div>
                    )}
                  </section>

                  <section className="space-y-3 border-t border-[var(--border)] pt-4">
                    <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Базовая роль</h4>
                    <Field
                      label="Роль"
                      htmlFor="employee-role"
                      hint="На следующем шаге любое право роли можно лично отключить или добавить сотруднику."
                    >
                      <Select id="employee-role" value={form.role} onChange={(e) => pickRole(e.target.value)}>
                        <option value="">Без роли</option>
                        {(roles ?? []).map((r) => (
                          <option key={r.id} value={r.id}>
                            {r.name}
                          </option>
                        ))}
                      </Select>
                    </Field>
                  </section>
                </>
              )}

              {employeeStep === 3 && (
                <section className="space-y-3 border-t border-[var(--border)] pt-4">
                  <h4 className="text-[12px] font-medium text-[var(--muted-foreground)]">Доступы</h4>
                  <div className="rounded-xl border bg-[var(--muted)]/35 px-3 py-2 text-xs text-[var(--muted-foreground)]">
                    Базовая роль:{" "}
                    <span className="font-semibold text-[var(--foreground)]">
                      {(roles ?? []).find((role) => String(role.id) === form.role)?.name ?? "Без роли"}
                    </span>
                    . Серые доступы наследуются из роли, перечёркнутые отключены лично.
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">К чему имеет доступ</span>
                      <span className="text-xs text-[var(--muted-foreground)]">
                        {rolePerms.size > 0 ? `Из роли: ${rolePerms.size - deniedCodes.size} · ` : ""}Личных:{" "}
                        {codes.size}
                        {deniedCodes.size > 0 ? ` · Запрещено: ${deniedCodes.size}` : ""}
                      </span>
                    </div>
                    <PermissionPicker
                      perms={perms ?? []}
                      selected={codes}
                      onToggle={toggleCode}
                      inherited={rolePerms}
                      denied={deniedCodes}
                      onToggleDenied={toggleDeniedCode}
                      forced={salesEmployee ? SALES_REQUIRED : undefined}
                    />
                  </div>
                </section>
              )}

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
        </>
      )}
    </AppShell>
  );
}

export default function EmployeesPage() {
  return (
    <RequirePerm perm={["employees.view", "rbac.view"]} title="Сотрудники">
      <EmployeesPageInner />
    </RequirePerm>
  );
}
