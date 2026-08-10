"use client";
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ErrorAlert } from "@/components/ui/data-state";
import { ActionMenu, type ActionMenuItem } from "@/components/ui/action-menu";
import { ActionCard } from "@/components/ui/action-card";
import { CurrencyAmounts } from "@/components/ui/currency-amounts";
import { Form, FormField, FormItem, FormLabel, FormControl, FormMessage } from "@/components/ui/form";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { usePagedApi } from "@/lib/use-paged-api";
import { LoadMore } from "@/components/ui/load-more";
import { api, apiError } from "@/lib/api";
import { finiteMoney } from "@/lib/currency-map";
import { cn, currencySymbol, formatPhone, formatMoney, formatDateTime, sumDebtByCurrency } from "@/lib/utils";
import { COUNTRIES } from "@/lib/countries";
import { BarChart3, FileSpreadsheet, KeyRound, Pencil, Phone, Plus, Search, Tags, Trash2 } from "lucide-react";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ALL_CLIENTS_STATEMENT_SECTIONS, StatementExportModal } from "@/components/statement-export-modal";
import type { Client, ReportSummary } from "@/lib/types";

const schema = z.object({
  first_name: z.string().min(2, "Введите имя (мин. 2 символа)"),
  last_name: z.string().trim().max(100, "Не более 100 символов"),
  company_name: z.string().optional(),
  phone: z.string().refine((v) => v.replace(/\D/g, "").length === 11, "Введите номер полностью"),
  country: z.string().optional(),
  iin: z
    .string()
    .optional()
    .refine((v) => !v || /^\d{12}$/.test(v), "ИИН/БИН — 12 цифр"),
  bank: z.string().optional(),
  bank_account: z.string().optional(),
  currency: z.enum(["KZT", "USD"]),
});
type FormValues = z.infer<typeof schema>;

function ClientForm({
  onDone,
  onCancel,
  editing,
}: {
  onDone: () => void;
  onCancel: () => void;
  editing?: Client | null;
}) {
  const [serverError, setServerError] = useState("");
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: editing
      ? {
          first_name: editing.first_name,
          last_name: editing.last_name,
          company_name: editing.company_name ?? "",
          phone: editing.phone,
          country: editing.country ?? "",
          iin: editing.iin ?? "",
          bank: editing.bank ?? "",
          bank_account: editing.bank_account ?? "",
          currency: editing.currency ?? "KZT",
        }
      : {
          first_name: "",
          last_name: "",
          company_name: "",
          phone: "",
          country: "",
          iin: "",
          bank: "",
          bank_account: "",
          currency: "KZT",
        },
  });

  async function onSubmit(values: FormValues) {
    setServerError("");
    try {
      if (editing) await api.patch(`/clients/${editing.id}/`, values);
      else await api.post("/clients/", values);
      onDone();
    } catch (e) {
      setServerError(apiError(e));
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="grid grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
        <FormField
          control={form.control}
          name="first_name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Имя</FormLabel>
              <FormControl>
                <Input autoFocus placeholder="Иван" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="last_name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                Фамилия <span className="font-normal text-[var(--muted-foreground)]">(необязательно)</span>
              </FormLabel>
              <FormControl>
                <Input placeholder="Петров" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="company_name"
          render={({ field }) => (
            <FormItem className="sm:col-span-2">
              <FormLabel>Название ТОО / ИП</FormLabel>
              <FormControl>
                <Input placeholder={'ТОО "Сайрам нан"'} {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="phone"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Номер телефона</FormLabel>
              <FormControl>
                <Input
                  type="tel"
                  inputMode="tel"
                  placeholder="+7 (___) ___-__-__"
                  value={field.value}
                  onChange={(e) => field.onChange(formatPhone(e.target.value))}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="country"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Страна</FormLabel>
              <Select value={field.value} onValueChange={field.onChange}>
                <FormControl>
                  <SelectTrigger>
                    <SelectValue placeholder="Выберите страну" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {COUNTRIES.map((c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormMessage />
            </FormItem>
          )}
        />

        <div className="sm:col-span-2 mt-1 border-t border-[var(--border)] pt-4 text-[12px] font-medium text-[var(--muted-foreground)]">
          Реквизиты
        </div>

        <FormField
          control={form.control}
          name="currency"
          render={({ field }) => (
            <FormItem className="sm:col-span-2 rounded-2xl border border-blue-100 bg-blue-50/55 p-4">
              <div className="flex flex-col justify-between gap-1 sm:flex-row sm:items-start sm:gap-6">
                <div>
                  <FormLabel>Валюта по умолчанию</FormLabel>
                  <p className="mt-1 text-xs leading-relaxed text-[var(--muted-foreground)]">
                    Предвыбирается в новом заказе. В личном прайсе цены в ₸ и $ хранятся отдельно.
                  </p>
                </div>
                <FormControl>
                  <div className="grid shrink-0 grid-cols-2 gap-1 rounded-xl border border-blue-100 bg-white p-1 shadow-sm">
                    {(["KZT", "USD"] as const).map((code) => (
                      <button
                        key={code}
                        type="button"
                        onClick={() => field.onChange(code)}
                        aria-pressed={field.value === code}
                        className={cn(
                          "min-w-28 rounded-lg px-3 py-2 text-left transition",
                          field.value === code
                            ? "bg-slate-900 text-white shadow-sm"
                            : "text-slate-500 hover:bg-slate-50 hover:text-slate-800",
                        )}
                      >
                        <span className="block text-xs font-bold">{code}</span>
                        <span
                          className={cn("block text-[10px]", field.value === code ? "text-white/60" : "text-slate-400")}
                        >
                          {code === "KZT" ? "тенге · ₸" : "доллар · $"}
                        </span>
                      </button>
                    ))}
                  </div>
                </FormControl>
              </div>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="iin"
          render={({ field }) => (
            <FormItem>
              <FormLabel>ИИН / БИН</FormLabel>
              <FormControl>
                <Input
                  inputMode="numeric"
                  placeholder="12 цифр"
                  maxLength={12}
                  value={field.value}
                  onChange={(e) => field.onChange(e.target.value.replace(/\D/g, "").slice(0, 12))}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="bank"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Банк</FormLabel>
              <FormControl>
                <Input placeholder="напр. Halyk Bank" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="bank_account"
          render={({ field }) => (
            <FormItem className="sm:col-span-2">
              <FormLabel>Расчётный счёт (IBAN)</FormLabel>
              <FormControl>
                <Input placeholder="KZ…" {...field} onChange={(e) => field.onChange(e.target.value.toUpperCase())} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        {serverError && (
          <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)] sm:col-span-2">
            {serverError}
          </p>
        )}

        <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:col-span-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28" onClick={onCancel}>
            Отмена
          </Button>
          <Button type="submit" className="w-full sm:w-auto sm:min-w-28" disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? "Сохранение…" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Form>
  );
}

const digits = (s: string) => s.replace(/\D/g, "");

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

      {error && (
        <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}

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
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  const [q, setQ] = useState("");
  const [iinQ, setIinQ] = useState("");
  const [phoneQ, setPhoneQ] = useState("");
  const [sortKey, setSortKey] = useState("created");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  // Страницы — только в «дефолтном» виде: поиск и пересортировка должны
  // видеть всех клиентов, поэтому в этих режимах грузим полный список.
  const searching = Boolean(q || iinQ || phoneQ);
  const usePaging = !searching && sortKey === "created" && sortDir === "desc";
  const paged = usePagedApi<Client>(usePaging ? "/clients/" : null, 50);
  const flat = useApi<Client[]>(usePaging ? null : "/clients/");
  const clients = usePaging ? paged.items : flat.data;
  const error = usePaging ? paged.error : flat.error;
  const reload = usePaging ? paged.reload : flat.reload;
  // Сводный долг обязан считаться по всем клиентам, а не по загруженной
  // странице — в ленивом режиме берём его из серверного отчёта.
  const { data: debtSummary } = useApi<ReportSummary>(usePaging && canMoney ? "/reports/summary/" : null);
  const [delItem, setDelItem] = useState<Client | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);
  const [purgeItem, setPurgeItem] = useState<Client | null>(null);
  const [purgeError, setPurgeError] = useState("");
  const [purgeBusy, setPurgeBusy] = useState(false);
  const [portalClient, setPortalClient] = useState<Client | null>(null);
  const [statementOpen, setStatementOpen] = useState(false);

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true);
    setDelError("");
    try {
      await api.delete(`/clients/${delItem.id}/`);
      setDelItem(null);
      reload();
    } catch (e) {
      setDelError(apiError(e));
    } finally {
      setDelBusy(false);
    }
  }

  async function confirmPurge() {
    if (!purgeItem) return;
    setPurgeBusy(true);
    setPurgeError("");
    try {
      await api.post(`/clients/${purgeItem.id}/purge/`);
      setPurgeItem(null);
      reload();
    } catch (e) {
      setPurgeError(apiError(e));
    } finally {
      setPurgeBusy(false);
    }
  }

  const list = clients ?? [];
  // Валюта долга берётся из заказов, а не из карточки клиента: у KZT-клиента
  // может быть заказ в USD, и складывать их в одну сумму нельзя.
  const debtByCurrency: Record<string, number> = !canMoney
    ? {}
    : usePaging
      ? Object.fromEntries(
          Object.entries(debtSummary?.debt_now.by_currency ?? {}).map(([currency, value]) => [
            currency,
            finiteMoney(value),
          ]),
        )
      : sumDebtByCurrency(list);

  const filtered = list.filter(
    (c) =>
      (!q || c.name.toLowerCase().includes(q.toLowerCase())) &&
      (!iinQ || (c.iin ?? "").includes(digits(iinQ))) &&
      (!phoneQ || digits(c.phone).includes(digits(phoneQ))),
  );

  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else {
      setSortKey(k);
      setSortDir("asc");
    }
  };
  const sorted = [...filtered].sort((a, b) => {
    if (sortKey === "debt") {
      const aCurrency = a.debt_currency ?? a.currency ?? "KZT";
      const bCurrency = b.debt_currency ?? b.currency ?? "KZT";
      const currencyCmp = aCurrency.localeCompare(bCurrency);
      const amountCmp = Number(a.debt_total ?? 0) - Number(b.debt_total ?? 0);
      const cmp = currencyCmp || amountCmp;
      return sortDir === "asc" ? cmp : -cmp;
    }
    let av: string | number = a.created_at ?? "";
    let bv: string | number = b.created_at ?? "";
    if (sortKey === "name") {
      av = a.name;
      bv = b.name;
    }
    const cmp = typeof av === "number" && typeof bv === "number" ? av - bv : String(av).localeCompare(String(bv), "ru");
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
            onSelect: () => {
              setDelError("");
              setDelItem(c);
            },
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
            onSelect: () => {
              setPurgeError("");
              setPurgeItem(c);
            },
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
        <div className="mb-5 flex flex-wrap gap-3">
          {(["KZT", "USD"] as const).map((currency) => (
            <div
              key={currency}
              className="inline-flex min-w-56 flex-col gap-1 rounded-xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-card"
            >
              <span className="text-[13px] font-medium text-[var(--muted-foreground)]">
                Общая задолженность · {currency}
              </span>
              <span className="text-[26px] font-bold leading-none tracking-tight tabular-nums text-[var(--destructive)]">
                {formatMoney(debtByCurrency[currency] ?? 0)} {currencySymbol(currency)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Фильтры — отдельные поля, как в референсе. */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по имени" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
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
      </div>

      {error && !clients && (
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
                      kind: "link",
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
                {sorted.length === 0 && (
                  <TR>
                    <TD colSpan={canMoney ? 6 : 5} className="py-14 text-center text-[var(--muted-foreground)]">
                      Здесь пусто
                    </TD>
                  </TR>
                )}
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
            onCancel={() => setOpen(false)}
            onDone={() => {
              setOpen(false);
              reload();
            }}
          />
        )}
      </Modal>

      <ConfirmDialog
        open={!!delItem}
        onClose={() => setDelItem(null)}
        title="Удалить клиента?"
        description={delItem ? `«${delItem.name}» будет удалён. Действие необратимо.` : ""}
        busy={delBusy}
        error={delError}
        onConfirm={confirmDelete}
      />
      <ConfirmDialog
        open={!!purgeItem}
        onClose={() => setPurgeItem(null)}
        title="Удалить клиента со всей историей?"
        description={
          purgeItem
            ? `«${purgeItem.name}» будет удалён вместе со всеми заказами, оплатами и счетами. Записи журнала останутся. Действие безвозвратно — используйте только для тестовых учёток.`
            : ""
        }
        confirmLabel="Удалить с историей"
        busy={purgeBusy}
        error={purgeError}
        onConfirm={confirmPurge}
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
        filename="clients-full-statement.xlsx"
        title="Общая выписка по клиентам"
        description="Единый Excel-файл по всей клиентской базе, заказам, продажам, оплатам и задолженности."
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
