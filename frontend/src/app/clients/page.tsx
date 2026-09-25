"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SearchInput } from "@/components/ui/search-input";
import { PasswordInput } from "@/components/ui/password-input";
import { PhoneInput } from "@/components/ui/phone-input";
import { Segmented } from "@/components/ui/segmented";
import { Modal } from "@/components/ui/modal";
import { EmptyRow, Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { SortableHeader, useSortState } from "@/components/ui/sortable-header";
import { FilterDropdown } from "@/components/ui/filter-dropdown";
import { ErrorAlert, FormError } from "@/components/ui/data-state";
import { ActionMenu, type ActionMenuItem } from "@/components/ui/action-menu";
import { ActionCard } from "@/components/ui/action-card";
import { StatCard } from "@/components/ui/stat-card";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { Field, fieldErrorId } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { useApi } from "@/lib/use-api";
import { useConfirmAction } from "@/lib/use-confirm-action";
import { usePagedApi } from "@/lib/use-paged-api";
import { LoadMore } from "@/components/ui/load-more";
import { api, apiError } from "@/lib/api";
import { cn, formatCurrency, formatDateTime, sumDebtByCurrency } from "@/lib/utils";
import { isPhoneComplete, onlyDigits } from "@/lib/phone";
import { COUNTRIES } from "@/lib/countries";
import { BarChart3, FileSpreadsheet, KeyRound, Pencil, Phone, Plus, Tags, Trash2 } from "lucide-react";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DepartmentDot } from "@/components/ui/department-badge";
import { Badge } from "@/components/ui/badge";
import { UnassignedClients } from "@/components/clients/unassigned-clients";
import { ALL_CLIENTS_STATEMENT_SECTIONS, StatementExportModal } from "@/components/statement-export-modal";
import type { Client, ClientDebt, Department, Me } from "@/lib/types";

type ClientFormValues = {
  first_name: string;
  last_name: string;
  company_name: string;
  phone: string;
  country: string;
  iin: string;
  bank: string;
  bank_account: string;
  currency: "KZT" | "USD";
  department: string;
};
type ClientFormKey = keyof ClientFormValues;
type ClientFormErrors = Partial<Record<ClientFormKey, string>>;

// Порядок полей на экране: фокус уходит на первое ошибочное.
const CLIENT_FIELD_ORDER: ClientFormKey[] = ["first_name", "last_name", "phone", "iin"];
const clientFieldId = (key: ClientFormKey) => `client-${key.replace("_", "-")}`;

function validateClient(values: ClientFormValues): ClientFormErrors {
  const errors: ClientFormErrors = {};
  if (values.first_name.length < 2) errors.first_name = "Введите имя (мин. 2 символа)";
  if (values.last_name.trim().length > 100) errors.last_name = "Не более 100 символов";
  if (!isPhoneComplete(values.phone)) errors.phone = "Введите номер полностью";
  if (values.iin && !/^\d{12}$/.test(values.iin)) errors.iin = "ИИН/БИН — 12 цифр";
  return errors;
}

function initialClientValues(editing: Client | null | undefined, assignedDepartment: Me["sales_department"]) {
  const department = assignedDepartment
    ? String(assignedDepartment.id)
    : editing?.department
      ? String(editing.department)
      : "";
  return {
    first_name: editing?.first_name ?? "",
    last_name: editing?.last_name ?? "",
    company_name: editing?.company_name ?? "",
    phone: editing?.phone ?? "",
    country: editing?.country ?? "",
    iin: editing?.iin ?? "",
    bank: editing?.bank ?? "",
    bank_account: editing?.bank_account ?? "",
    currency: editing?.currency ?? "KZT",
    department,
  } satisfies ClientFormValues;
}

function ClientForm({
  onDone,
  onCancel,
  editing,
  assignedDepartment,
}: {
  onDone: () => void;
  onCancel: () => void;
  editing?: Client | null;
  assignedDepartment: Me["sales_department"];
}) {
  const [values, setValues] = useState<ClientFormValues>(() => initialClientValues(editing, assignedDepartment));
  const [errors, setErrors] = useState<ClientFormErrors>({});
  const [serverError, setServerError] = useState("");
  const [busy, setBusy] = useState(false);
  const departmentChoiceTouched = useRef(Boolean(editing || assignedDepartment));
  const {
    data: departments,
    loading: departmentsLoading,
    error: departmentsError,
    reload: reloadDepartments,
  } = useApi<Department[]>(assignedDepartment ? null : "/departments/?all=1");

  function set<K extends ClientFormKey>(key: K, value: ClientFormValues[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => (current[key] ? { ...current, [key]: undefined } : current));
  }
  const upd = (key: ClientFormKey) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    set(key, e.target.value);

  // Общие атрибуты ошибки для контрола поля.
  const invalidProps = (key: ClientFormKey) =>
    errors[key] ? { "aria-invalid": true, "aria-describedby": fieldErrorId(clientFieldId(key)) } : {};

  // Пока отделы грузятся (или не загрузились), текущий отдел клиента некому
  // показать в поле. Архивные отделы приходят в основном списке (?all=1).
  const currentDepartmentMissing =
    editing?.department != null && !(departments ?? []).some((row) => row.id === editing.department);

  useEffect(() => {
    if (editing || assignedDepartment || !departments || departmentChoiceTouched.current) return;
    const defaultDepartment =
      departments.find((department) => department.is_active && department.is_default) ??
      departments.find((department) => department.is_active);
    if (defaultDepartment) setValues((current) => ({ ...current, department: String(defaultDepartment.id) }));
    departmentChoiceTouched.current = true;
  }, [assignedDepartment, departments, editing]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setServerError("");
    const invalid = validateClient(values);
    const first = CLIENT_FIELD_ORDER.find((key) => invalid[key]);
    if (first) {
      setErrors(invalid);
      requestAnimationFrame(() => document.getElementById(clientFieldId(first))?.focus());
      return;
    }
    setBusy(true);
    try {
      const { department, ...clientValues } = values;
      const payload = {
        ...clientValues,
        last_name: clientValues.last_name.trim(),
        department: assignedDepartment?.id ?? (department ? Number(department) : null),
      };
      if (editing) await api.patch(`/clients/${editing.id}/`, payload);
      else await api.post("/clients/", payload);
      onDone();
    } catch (e) {
      setServerError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} noValidate className="grid grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
      <Field label="Имя" htmlFor={clientFieldId("first_name")} error={errors.first_name}>
        <Input
          id={clientFieldId("first_name")}
          autoFocus
          placeholder="Иван"
          value={values.first_name}
          onChange={upd("first_name")}
          {...invalidProps("first_name")}
        />
      </Field>

      <Field
        label={
          <>
            Фамилия <span className="font-normal text-[var(--muted-foreground)]">(необязательно)</span>
          </>
        }
        htmlFor={clientFieldId("last_name")}
        error={errors.last_name}
      >
        <Input
          id={clientFieldId("last_name")}
          placeholder="Петров"
          value={values.last_name}
          onChange={upd("last_name")}
          {...invalidProps("last_name")}
        />
      </Field>

      <Field label="Название ТОО / ИП" htmlFor={clientFieldId("company_name")} className="sm:col-span-2">
        <Input
          id={clientFieldId("company_name")}
          placeholder={'ТОО "Сайрам нан"'}
          value={values.company_name}
          onChange={upd("company_name")}
        />
      </Field>

      <Field label="Номер телефона" htmlFor={clientFieldId("phone")} error={errors.phone}>
        <PhoneInput
          id={clientFieldId("phone")}
          value={values.phone}
          onChange={(value) => set("phone", value)}
          defaultCountry={editing?.country}
          onCountryChange={(country) => setValues((current) => (current.country ? current : { ...current, country }))}
          {...invalidProps("phone")}
        />
      </Field>

      <Field label="Страна" htmlFor={clientFieldId("country")}>
        <Select id={clientFieldId("country")} value={values.country} onChange={upd("country")}>
          <option value="">Выберите страну</option>
          {COUNTRIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      </Field>

      <Field
        label={
          <>
            Отдел продаж{" "}
            {!assignedDepartment && <span className="font-normal text-[var(--muted-foreground)]">(необязательно)</span>}
          </>
        }
        htmlFor={assignedDepartment ? undefined : clientFieldId("department")}
        className="sm:col-span-2"
      >
        {assignedDepartment ? (
          <div className="flex h-10 items-center gap-2 rounded-md border bg-[var(--muted)]/35 px-3.5 text-sm font-medium">
            <DepartmentDot color={assignedDepartment.color} className="size-2" />
            {assignedDepartment.name}
          </div>
        ) : (
          <Select
            id={clientFieldId("department")}
            value={values.department}
            onChange={(e) => {
              departmentChoiceTouched.current = true;
              set("department", e.target.value);
            }}
            disabled={departmentsLoading && !departments}
          >
            <option value="">Без отдела</option>
            {currentDepartmentMissing && editing?.department && (
              <option value={String(editing.department)}>
                {editing.department_name || `Отдел #${editing.department}`}
              </option>
            )}
            {(departments ?? []).map((department) => (
              <option
                key={department.id}
                value={String(department.id)}
                disabled={!department.is_active && department.id !== editing?.department}
              >
                {department.name}
                {!department.is_active && " (архивный)"}
              </option>
            ))}
          </Select>
        )}
        <p className="mt-1.5 text-xs text-[var(--muted-foreground)]">
          {assignedDepartment
            ? "Клиент закрепляется за вашим отделом."
            : "Клиента можно оставить без ответственного отдела."}
        </p>
        {departmentsError && (
          <div className="mt-1.5 flex items-center justify-between gap-3 text-xs text-[var(--destructive)]">
            <span>{departmentsError}</span>
            <Button type="button" size="sm" variant="link" className="h-auto px-0" onClick={reloadDepartments}>
              Повторить
            </Button>
          </div>
        )}
      </Field>

      <div className="sm:col-span-2 mt-1 border-t border-[var(--border)] pt-4 text-[12px] font-medium text-[var(--muted-foreground)]">
        Реквизиты
      </div>

      <div className="sm:col-span-2 rounded-2xl border border-blue-100 bg-blue-50/55 p-4">
        <div className="flex flex-col justify-between gap-1 sm:flex-row sm:items-start sm:gap-6">
          <div>
            <p className="text-[12px] font-medium text-[var(--foreground)]">Валюта по умолчанию</p>
            <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
              Предвыбирается в новом заказе. В личном прайсе цены в ₸ и $ хранятся отдельно.
            </p>
          </div>
          <div className="shrink-0 sm:w-60">
            <Segmented
              ariaLabel="Валюта по умолчанию"
              value={values.currency}
              onChange={(currency) => set("currency", currency)}
              options={[
                { value: "KZT", label: "KZT", caption: "тенге · ₸" },
                { value: "USD", label: "USD", caption: "доллар · $" },
              ]}
            />
          </div>
        </div>
      </div>

      <Field label="ИИН / БИН" htmlFor={clientFieldId("iin")} error={errors.iin}>
        <Input
          id={clientFieldId("iin")}
          inputMode="numeric"
          placeholder="12 цифр"
          maxLength={12}
          value={values.iin}
          onChange={(e) => set("iin", onlyDigits(e.target.value).slice(0, 12))}
          {...invalidProps("iin")}
        />
      </Field>

      <Field label="Банк" htmlFor={clientFieldId("bank")}>
        <Input id={clientFieldId("bank")} placeholder="напр. Halyk Bank" value={values.bank} onChange={upd("bank")} />
      </Field>

      <Field label="Расчётный счёт (IBAN)" htmlFor={clientFieldId("bank_account")} className="sm:col-span-2">
        <Input
          id={clientFieldId("bank_account")}
          placeholder="KZ…"
          value={values.bank_account}
          onChange={(e) => set("bank_account", e.target.value.toUpperCase())}
        />
      </Field>

      <FormError message={serverError} className="sm:col-span-2" />

      <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:col-span-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="submit" className="w-full sm:w-auto sm:min-w-28" disabled={busy}>
          {busy ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
    </form>
  );
}

const WaitingDepartmentBadge = () => (
  <Badge tone="warning" dot>
    Ждёт отдела
  </Badge>
);

function ClientPortalAccessForm({
  client,
  onDone,
  onCancel,
}: {
  client: Client;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError("");
    if (password !== confirmation) {
      setError("Пароли не совпадают.");
      return;
    }

    setBusy(true);
    try {
      await api.post(`/clients/${client.id}/password/`, { password });
      setPassword("");
      setConfirmation("");
      onDone();
    } catch (caught) {
      setError(apiError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div className="rounded-2xl border border-blue-100 bg-blue-50/60 p-4">
        <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-blue-500">Логин клиента</div>
        <div className="mt-1 font-mono text-base font-semibold text-slate-900">{client.username}</div>
        <p className="mt-2 text-xs leading-relaxed text-slate-500">
          После первого входа клиент обязательно заменит временный пароль. Старые сессии будут отозваны.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-1.5 text-sm font-medium">
          <span>Временный пароль</span>
          <PasswordInput
            value={password}
            minLength={8}
            required
            autoComplete="new-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        <label className="space-y-1.5 text-sm font-medium">
          <span>Повторите пароль</span>
          <PasswordInput
            value={confirmation}
            minLength={8}
            required
            autoComplete="new-password"
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </label>
      </div>

      <FormError message={error} />

      <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          Отмена
        </Button>
        <Button type="submit" disabled={busy}>
          <KeyRound className="size-4" /> {busy ? "Сохранение…" : "Выдать временный пароль"}
        </Button>
      </div>
    </form>
  );
}

function ClientsPageInner() {
  const router = useRouter();
  const { me } = useAuth();
  const canCreate = can(me, "clients.create");
  const canEdit = can(me, "clients.edit");
  const canDelete = can(me, "clients.delete");
  const canSetPrice = can(me, "clients.set_price");
  const canManagePortalAccess = can(me, "clients.manage_access");
  const canMoney = can(me, "reports.view"); // финансовая аналитика — под reports.view
  const canExport = can(me, "reports.export");
  // Закрепить клиента без отдела может и касса: она разбирает заявки саморегистрации.
  const canAssignDepartment = canEdit || can(me, "orders.confirm");
  const assignedDepartment = me?.sales_department ?? null;
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  const [q, setQ] = useState("");
  const [iinQ, setIinQ] = useState("");
  const [phoneQ, setPhoneQ] = useState("");
  const [department, setDepartment] = useState("all");
  const { sortKey, sortDir, toggleSort } = useSortState("created", "desc");
  const effectiveDepartment = assignedDepartment?.code ?? department;
  const clientsUrl =
    effectiveDepartment === "all" ? "/clients/" : `/clients/?department=${encodeURIComponent(effectiveDepartment)}`;
  // Страницы — только в «дефолтном» виде: поиск и пересортировка должны
  // видеть всех клиентов, поэтому в этих режимах грузим полный список.
  const searching = Boolean(q || iinQ || phoneQ);
  const usePaging = !searching && sortKey === "created" && sortDir === "desc";
  const paged = usePagedApi<Client>(usePaging ? clientsUrl : null, 50);
  const flat = useApi<Client[]>(usePaging ? null : clientsUrl);
  const clients = usePaging ? paged.items : flat.data;
  const error = usePaging ? paged.error : flat.error;
  const reload = usePaging ? paged.reload : flat.reload;
  // Фильтр по отделу виден только сотруднику без закреплённого отдела.
  const { data: departments } = useApi<Department[]>(assignedDepartment ? null : "/departments/");
  // Клиентский отдел и отдел заказа независимы. Поэтому шапку считаем по
  // ownership клиента, а не через reports/summary?department=...
  const clientDebtsUrl =
    effectiveDepartment === "all"
      ? "/clients/debts/"
      : `/clients/debts/?client_department=${encodeURIComponent(effectiveDepartment)}`;
  const {
    data: clientDebts,
    error: clientDebtsError,
    reload: reloadClientDebts,
  } = useApi<ClientDebt[]>(canMoney ? clientDebtsUrl : null);
  const [portalClient, setPortalClient] = useState<Client | null>(null);
  const [statementOpen, setStatementOpen] = useState(false);

  function reloadClients() {
    reload();
    void reloadClientDebts();
  }
  const del = useConfirmAction<Client>(async (client) => {
    await api.delete(`/clients/${client.id}/`);
    reloadClients();
  });
  const purge = useConfirmAction<Client>(async (client) => {
    await api.post(`/clients/${client.id}/purge/`);
    reloadClients();
  });

  const list = clients ?? [];
  // Валюта долга берётся из заказов, а не из карточки клиента: у KZT-клиента
  // может быть заказ в USD, и складывать их в одну сумму нельзя.
  const debtByCurrency: Record<string, number> = canMoney ? sumDebtByCurrency(clientDebts ?? []) : {};

  const filtered = list.filter(
    (c) =>
      (!q || c.name.toLowerCase().includes(q.toLowerCase())) &&
      (!iinQ || c.iin.includes(onlyDigits(iinQ))) &&
      (!phoneQ || onlyDigits(c.phone).includes(onlyDigits(phoneQ))),
  );

  const sorted = [...filtered].sort((a, b) => {
    if (sortKey === "debt") {
      const aCurrency = a.debt_currency ?? a.currency;
      const bCurrency = b.debt_currency ?? b.currency;
      const currencyCmp = aCurrency.localeCompare(bCurrency);
      const amountCmp = Number(a.debt_total ?? 0) - Number(b.debt_total ?? 0);
      const cmp = currencyCmp || amountCmp;
      return sortDir === "asc" ? cmp : -cmp;
    }
    const cmp =
      sortKey === "name"
        ? a.name.localeCompare(b.name, "ru")
        : (a.created_at ?? "").localeCompare(b.created_at ?? "", "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  const rowMenu = (c: Client): ActionMenuItem[] => [
    ...(canMoney
      ? [{ key: "open", label: "Открыть", icon: BarChart3, onSelect: () => router.push(`/clients/${c.id}`) }]
      : []),
    ...(canSetPrice
      ? [
          {
            key: "prices",
            label: "Прайс-лист",
            icon: Tags,
            onSelect: () => router.push(`/clients/${c.id}/prices`),
          },
        ]
      : []),
    ...(canEdit
      ? [
          {
            key: "edit",
            label: "Изменить",
            icon: Pencil,
            onSelect: () => {
              setEditing(c);
              setOpen(true);
            },
          },
        ]
      : []),
    ...(canManagePortalAccess
      ? [
          {
            key: "portal-access",
            label: c.portal_access_enabled ? "Сбросить пароль портала" : "Выдать доступ в портал",
            icon: KeyRound,
            onSelect: () => setPortalClient(c),
          },
        ]
      : []),
    ...(canDelete
      ? [
          {
            key: "delete",
            label: "Удалить",
            icon: Trash2,
            tone: "destructive" as const,
            onSelect: () => del.open(c),
          },
        ]
      : []),
    // Зачистка тестовых учёток: обычное удаление блокируют заказы (PROTECT).
    ...(me?.is_superuser
      ? [
          {
            key: "purge",
            label: "Удалить с историей",
            icon: Trash2,
            tone: "destructive" as const,
            onSelect: () => purge.open(c),
          },
        ]
      : []),
  ];

  return (
    <AppShell
      title="Клиенты"
      section="Работа"
      description="Клиентская база: контакты, реквизиты и задолженность по каждому клиенту."
      actions={
        (canCreate || canExport) && (
          <div className="flex items-center gap-2">
            {canExport && (
              <Button
                size="sm"
                variant="outline"
                aria-label="Общая Excel-выписка"
                onClick={() => setStatementOpen(true)}
              >
                <FileSpreadsheet className="size-4 text-emerald-600" />
                <span className="hidden sm:inline">Общая выписка</span>
              </Button>
            )}
            {canCreate && (
              <Button
                size="sm"
                aria-label="Добавить клиента"
                onClick={() => {
                  setEditing(null);
                  setOpen(true);
                }}
              >
                <Plus className="size-4" /> <span className="hidden sm:inline">Добавить клиента</span>
              </Button>
            )}
          </div>
        )
      }
    >
      {canMoney && (
        /* Общая задолженность доступна только финансовой роли. */
        <div className="mb-5 grid grid-cols-2 gap-3 sm:max-w-xl">
          {(["KZT", "USD"] as const).map((currency) => (
            <StatCard
              key={currency}
              label={`Общая задолженность · ${currency}`}
              value={formatCurrency(debtByCurrency[currency] ?? 0, currency)}
              tone="destructive"
            />
          ))}
        </div>
      )}

      {canAssignDepartment && (
        <UnassignedClients
          ownDepartment={assignedDepartment}
          departments={departments ?? []}
          onAssigned={() => {
            void reload();
            if (canMoney) void reloadClientDebts();
          }}
        />
      )}

      {clientDebtsError && (
        <div className="mb-4">
          <ErrorAlert message={clientDebtsError} onRetry={reloadClientDebts} />
        </div>
      )}

      {/* Фильтры — отдельные поля, как в референсе. */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <SearchInput
          wrapperClassName="w-full sm:w-64"
          placeholder="Поиск по имени"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Input
          className="w-full sm:w-44"
          placeholder="ИИН/БИН"
          inputMode="numeric"
          value={iinQ}
          onChange={(e) => setIinQ(e.target.value)}
        />
        <Input
          className="w-full sm:w-48"
          placeholder="+7 (XXX) XXX-XXXX"
          inputMode="tel"
          value={phoneQ}
          onChange={(e) => setPhoneQ(e.target.value)}
        />
        {assignedDepartment ? (
          <div className="flex h-9 items-center gap-2 rounded-md border bg-[var(--primary)]/5 px-3 text-[13px]">
            <span className="text-[var(--muted-foreground)]">Отдел:</span>
            <DepartmentDot color={assignedDepartment.color} className="size-2" />
            <span className="font-medium">{assignedDepartment.name}</span>
          </div>
        ) : (
          <FilterDropdown
            label="Отдел"
            active={department}
            onChange={setDepartment}
            options={[
              { key: "all", label: "Все" },
              { key: "none", label: "Без отдела" },
              ...(departments ?? []).map((row) => ({ key: row.code, label: row.name })),
            ]}
          />
        )}
      </div>

      {error && (
        <div className="mb-4">
          <ErrorAlert message={error} onRetry={reload} />
        </div>
      )}

      {/* Мобильные карточки: таблица на телефоне нечитаемая. */}
      <div className="flex flex-col gap-3 md:hidden">
        {sorted.length === 0 ? (
          <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Здесь пусто</p>
        ) : (
          sorted.map((c) => (
            <ActionCard
              key={c.id}
              primaryAction={
                canMoney
                  ? {
                      href: `/clients/${c.id}`,
                      label: `Открыть клиента ${c.name}`,
                    }
                  : undefined
              }
              className={cn(
                "flex flex-col gap-2.5 rounded-xl border bg-[var(--card)] p-4 shadow-card",
                canMoney && "cursor-pointer",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-semibold">{c.name}</div>
                  <div className="text-xs text-[var(--muted-foreground)]">
                    {c.department_name || <WaitingDepartmentBadge />}
                  </div>
                  <a
                    href={`tel:${c.phone}`}
                    className="relative z-10 flex items-center gap-1.5 text-sm text-[var(--muted-foreground)]"
                  >
                    <Phone className="size-3.5" /> {c.phone}
                  </a>
                </div>
                <div className="relative z-10">
                  <ActionMenu items={rowMenu(c)} />
                </div>
              </div>
              <div className={cn("grid gap-2 text-sm", canMoney ? "grid-cols-2" : "grid-cols-1")}>
                {canMoney && (
                  <div>
                    <div className="text-[11px] text-[var(--muted-foreground)]">Задолженность</div>
                    <div className="font-medium tabular-nums text-[var(--destructive)]">
                      <CurrencyAmounts
                        byCurrency={c.debt_by_currency}
                        fallbackAmount={c.debt_total}
                        fallbackCurrency={c.debt_currency ?? c.currency}
                      />
                    </div>
                  </div>
                )}
                <div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">Дата</div>
                  <div className="tabular-nums">{c.created_at ? formatDateTime(c.created_at) : "—"}</div>
                </div>
              </div>
            </ActionCard>
          ))
        )}
      </div>

      <div className="hidden md:block">
        <Card>
          <CardContent className="pt-6">
            <Table>
              <THead>
                <TR>
                  <SortableHeader label="Имя" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH>Отдел</TH>
                  <TH>ИИН/БИН</TH>
                  <TH>Телефон</TH>
                  {canMoney && (
                    <SortableHeader
                      label="Сумма задолженностей"
                      sortKey="debt"
                      activeKey={sortKey}
                      dir={sortDir}
                      onClick={toggleSort}
                    />
                  )}
                  <SortableHeader
                    label="Дата"
                    sortKey="created"
                    activeKey={sortKey}
                    dir={sortDir}
                    onClick={toggleSort}
                  />
                  <TH></TH>
                </TR>
              </THead>
              <TBody>
                {sorted.map((c) => (
                  <TR
                    key={c.id}
                    onClick={canMoney ? () => router.push(`/clients/${c.id}`) : undefined}
                    className={canMoney ? "cursor-pointer hover:bg-[var(--muted)]/40" : ""}
                  >
                    <TD>
                      {canMoney ? (
                        <Link
                          href={`/clients/${c.id}`}
                          onClick={(e) => e.stopPropagation()}
                          className="font-medium text-[var(--ring)] hover:underline"
                        >
                          {c.name}
                        </Link>
                      ) : (
                        <span className="font-medium">{c.name}</span>
                      )}
                    </TD>
                    <TD className="text-[var(--muted-foreground)]">
                      {c.department_name || <WaitingDepartmentBadge />}
                    </TD>
                    <TD className="tabular-nums">{c.iin || "—"}</TD>
                    <TD className="tabular-nums">{c.phone}</TD>
                    {canMoney && (
                      <TD className="tabular-nums">
                        <span className="font-medium text-[var(--destructive)]">
                          <CurrencyAmounts
                            byCurrency={c.debt_by_currency}
                            fallbackAmount={c.debt_total}
                            fallbackCurrency={c.debt_currency ?? c.currency}
                          />
                        </span>
                      </TD>
                    )}
                    <TD className="tabular-nums text-[var(--muted-foreground)]">
                      {c.created_at ? formatDateTime(c.created_at) : "—"}
                    </TD>
                    <TD onClick={(e) => e.stopPropagation()}>
                      <div className="flex justify-end">
                        <ActionMenu items={rowMenu(c)} />
                      </div>
                    </TD>
                  </TR>
                ))}
                {sorted.length === 0 && <EmptyRow colSpan={canMoney ? 7 : 6} />}
              </TBody>
            </Table>
            {usePaging && (
              <LoadMore
                shown={list.length}
                total={paged.count}
                hasMore={paged.hasMore}
                loading={paged.loadingMore}
                onClick={paged.loadMore}
              />
            )}
          </CardContent>
        </Card>
      </div>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow={editing ? "Работа · Изменение" : "Работа · Клиент"}
        title={editing ? "Изменить клиента" : "Новый клиент"}
        description="Контакты и платёжные реквизиты клиента."
        className="max-w-xl"
      >
        {open && (
          <ClientForm
            editing={editing}
            assignedDepartment={assignedDepartment}
            onCancel={() => setOpen(false)}
            onDone={() => {
              setOpen(false);
              reloadClients();
            }}
          />
        )}
      </Modal>

      <ConfirmDialog
        {...del.dialog}
        title="Удалить клиента?"
        description={del.item ? `«${del.item.name}» будет удалён. Действие необратимо.` : ""}
      />
      <ConfirmDialog
        {...purge.dialog}
        title="Удалить клиента со всей историей?"
        description={
          purge.item
            ? `«${purge.item.name}» будет удалён вместе со всеми заказами, оплатами и счетами. Записи журнала останутся. Действие безвозвратно — используйте только для тестовых учёток.`
            : ""
        }
        confirmLabel="Удалить с историей"
      />
      <Modal
        open={!!portalClient}
        onClose={() => setPortalClient(null)}
        eyebrow="Безопасность · Портал"
        title={portalClient?.portal_access_enabled ? "Сбросить пароль клиента" : "Выдать доступ клиенту"}
        description={portalClient ? `Личный кабинет для «${portalClient.name}».` : ""}
        className="max-w-xl"
      >
        {portalClient && (
          <ClientPortalAccessForm
            client={portalClient}
            onCancel={() => setPortalClient(null)}
            onDone={() => {
              setPortalClient(null);
              reload();
            }}
          />
        )}
      </Modal>
      <StatementExportModal
        open={statementOpen}
        onClose={() => setStatementOpen(false)}
        endpoint="/clients/statement/"
        filenameStem="clients-full-statement"
        title="Общая выписка по клиентам"
        description="Единая выписка в Excel или PDF по всей клиентской базе, заказам, продажам, оплатам и задолженности."
        scopeLabel="Все клиенты и все финансовые движения"
        sections={ALL_CLIENTS_STATEMENT_SECTIONS}
      />
    </AppShell>
  );
}

export default function ClientsPage() {
  return (
    <RequirePerm perm="clients.view" title="Клиенты">
      <ClientsPageInner />
    </RequirePerm>
  );
}
