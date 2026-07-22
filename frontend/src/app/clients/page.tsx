"use client";
import { useEffect, useRef, useState } from "react";
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
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { ErrorAlert } from "@/components/ui/data-state";
import {
  Form, FormField, FormItem, FormLabel, FormControl, FormMessage,
} from "@/components/ui/form";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { cn, currencySymbol, formatPhone, formatMoney, formatDateTime } from "@/lib/utils";
import { COUNTRIES } from "@/lib/countries";
import {
  BarChart3, FileSpreadsheet, MoreVertical, Pencil, Phone, Plus, Search, Tags, Trash2,
} from "lucide-react";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { StatementExportModal } from "@/components/statement-export-modal";
import type { Client } from "@/lib/types";

const schema = z.object({
  first_name: z.string().min(2, "Введите имя (мин. 2 символа)"),
  last_name: z.string().trim().max(100, "Не более 100 символов"),
  company_name: z.string().optional(),
  phone: z
    .string()
    .refine((v) => v.replace(/\D/g, "").length === 11, "Введите номер полностью"),
  country: z.string().optional(),
  iin: z.string().optional().refine(
    (v) => !v || /^\d{12}$/.test(v), "ИИН/БИН — 12 цифр"
  ),
  bank: z.string().optional(),
  bank_account: z.string().optional(),
  currency: z.enum(["KZT", "USD"]),
});
type FormValues = z.infer<typeof schema>;

function ClientForm({ onDone, onCancel, editing }: { onDone: () => void; onCancel: () => void; editing?: Client | null }) {
  const [serverError, setServerError] = useState("");
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: editing ? {
      first_name: editing.first_name, last_name: editing.last_name,
      company_name: editing.company_name ?? "", phone: editing.phone,
      country: editing.country ?? "", iin: editing.iin ?? "",
      bank: editing.bank ?? "", bank_account: editing.bank_account ?? "",
      currency: editing.currency ?? "KZT",
    } : {
      first_name: "", last_name: "", company_name: "", phone: "", country: "",
      iin: "", bank: "", bank_account: "",
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
      <form onSubmit={form.handleSubmit(onSubmit)}
        className="grid grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
        <FormField control={form.control} name="first_name" render={({ field }) => (
          <FormItem>
            <FormLabel>Имя</FormLabel>
            <FormControl><Input autoFocus placeholder="Иван" {...field} /></FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="last_name" render={({ field }) => (
          <FormItem>
            <FormLabel>Фамилия <span className="font-normal text-[var(--muted-foreground)]">(необязательно)</span></FormLabel>
            <FormControl><Input placeholder="Петров" {...field} /></FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="company_name" render={({ field }) => (
          <FormItem className="sm:col-span-2">
            <FormLabel>Название ТОО / ИП</FormLabel>
            <FormControl><Input placeholder={'ТОО "Сайрам нан"'} {...field} /></FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="phone" render={({ field }) => (
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
        )} />

        <FormField control={form.control} name="country" render={({ field }) => (
          <FormItem>
            <FormLabel>Страна</FormLabel>
            <Select value={field.value} onValueChange={field.onChange}>
              <FormControl>
                <SelectTrigger>
                  <SelectValue placeholder="Выберите страну" />
                </SelectTrigger>
              </FormControl>
              <SelectContent>
                {COUNTRIES.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
              </SelectContent>
            </Select>
            <FormMessage />
          </FormItem>
        )} />

        <div className="sm:col-span-2 mt-1 border-t border-[var(--border)] pt-4 text-[12px] font-medium text-[var(--muted-foreground)]">
          Реквизиты
        </div>

        <FormField control={form.control} name="currency" render={({ field }) => (
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
                    <button key={code} type="button" onClick={() => field.onChange(code)}
                      aria-pressed={field.value === code}
                      className={cn(
                        "min-w-28 rounded-lg px-3 py-2 text-left transition",
                        field.value === code
                          ? "bg-slate-900 text-white shadow-sm"
                          : "text-slate-500 hover:bg-slate-50 hover:text-slate-800",
                      )}>
                      <span className="block text-xs font-bold">{code}</span>
                      <span className={cn("block text-[10px]", field.value === code ? "text-white/60" : "text-slate-400")}>
                        {code === "KZT" ? "тенге · ₸" : "доллар · $"}
                      </span>
                    </button>
                  ))}
                </div>
              </FormControl>
            </div>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="iin" render={({ field }) => (
          <FormItem>
            <FormLabel>ИИН / БИН</FormLabel>
            <FormControl>
              <Input inputMode="numeric" placeholder="12 цифр" maxLength={12}
                value={field.value}
                onChange={(e) => field.onChange(e.target.value.replace(/\D/g, "").slice(0, 12))} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="bank" render={({ field }) => (
          <FormItem>
            <FormLabel>Банк</FormLabel>
            <FormControl>
              <Input placeholder="напр. Halyk Bank" {...field} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )} />

        <FormField control={form.control} name="bank_account" render={({ field }) => (
          <FormItem className="sm:col-span-2">
            <FormLabel>Расчётный счёт (IBAN)</FormLabel>
            <FormControl>
              <Input placeholder="KZ…" {...field}
                onChange={(e) => field.onChange(e.target.value.toUpperCase())} />
            </FormControl>
            <FormMessage />
          </FormItem>
        )} />

        {serverError && (
          <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)] sm:col-span-2">
            {serverError}
          </p>
        )}

        <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:col-span-2 sm:flex-row sm:justify-end">
          <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28"
            onClick={onCancel}>Отмена</Button>
          <Button type="submit" className="w-full sm:w-auto sm:min-w-28"
            disabled={form.formState.isSubmitting}>
            {form.formState.isSubmitting ? "Сохранение…" : "Сохранить"}
          </Button>
        </div>
      </form>
    </Form>
  );
}

/** Меню действий строки — «⋮», как в референсе. */
function RowMenu({ items }: {
  items: { label: string; icon: React.ElementType; onClick: () => void; danger?: boolean }[];
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (items.length === 0) return null;
  return (
    <div ref={ref} className="relative inline-block">
      <button type="button" onClick={() => setOpen(!open)} aria-label="Действия"
        className="flex size-8 items-center justify-center rounded-md text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)] hover:text-[var(--foreground)]">
        <MoreVertical className="size-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-20 min-w-44 rounded-lg border border-[var(--border)] bg-[var(--card)] p-1 shadow-lg">
          {items.map((it) => (
            <button key={it.label} type="button"
              onClick={() => { setOpen(false); it.onClick(); }}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-[var(--muted)]",
                it.danger && "text-[var(--destructive)]",
              )}>
              <it.icon className="size-4" /> {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const digits = (s: string) => s.replace(/\D/g, "");

function ClientsPageInner() {
  const router = useRouter();
  const { data: clients, error, reload } = useApi<Client[]>("/clients/");
  const { me } = useAuth();
  const canCreate = can(me, "clients.create");
  const canEdit = can(me, "clients.edit");
  const canDelete = can(me, "clients.delete");
  const canSetPrice = can(me, "clients.set_price");
  const canMoney = can(me, "reports.view");  // финансовая аналитика — под reports.view
  const canExport = can(me, "reports.export");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  const [q, setQ] = useState("");
  const [iinQ, setIinQ] = useState("");
  const [phoneQ, setPhoneQ] = useState("");
  const [sortKey, setSortKey] = useState("created");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [delItem, setDelItem] = useState<Client | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);
  const [statementOpen, setStatementOpen] = useState(false);

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true); setDelError("");
    try {
      await api.delete(`/clients/${delItem.id}/`);
      setDelItem(null); reload();
    } catch (e) { setDelError(apiError(e)); } finally { setDelBusy(false); }
  }

  const list = clients ?? [];
  const debtByCurrency = list.reduce<Record<string, number>>((totals, client) => {
    totals[client.currency] = (totals[client.currency] ?? 0) + Number(client.debt_total ?? 0);
    return totals;
  }, {});

  const filtered = list.filter((c) =>
    (!q || c.name.toLowerCase().includes(q.toLowerCase()))
    && (!iinQ || (c.iin ?? "").includes(digits(iinQ)))
    && (!phoneQ || digits(c.phone).includes(digits(phoneQ))));

  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const sorted = [...filtered].sort((a, b) => {
    let av: string | number = a.created_at ?? "";
    let bv: string | number = b.created_at ?? "";
    if (sortKey === "name") { av = a.name; bv = b.name; }
    if (sortKey === "debt") { av = Number(a.debt_total ?? 0); bv = Number(b.debt_total ?? 0); }
    const cmp = typeof av === "number" && typeof bv === "number"
      ? av - bv
      : String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  const rowMenu = (c: Client) => [
    ...(canMoney ? [{ label: "Открыть", icon: BarChart3, onClick: () => router.push(`/clients/${c.id}`) }] : []),
    ...(canSetPrice ? [{ label: "Прайс-лист", icon: Tags, onClick: () => router.push(`/clients/${c.id}/prices`) }] : []),
    ...(canEdit ? [{ label: "Изменить", icon: Pencil, onClick: () => { setEditing(c); setOpen(true); } }] : []),
    ...(canDelete ? [{ label: "Удалить", icon: Trash2, danger: true, onClick: () => { setDelError(""); setDelItem(c); } }] : []),
  ];

  return (
    <AppShell title="Клиенты" section="Работа" description="Клиентская база: контакты, реквизиты и задолженность по каждому клиенту."
      actions={(canCreate || canExport) && (
        <div className="flex items-center gap-2">
          {canExport && (
            <Button size="sm" variant="outline" aria-label="Общая Excel-выписка"
              onClick={() => setStatementOpen(true)}>
              <FileSpreadsheet className="size-4 text-emerald-600" />
              <span className="hidden sm:inline">Общая выписка</span>
            </Button>
          )}
          {canCreate && (
            <Button size="sm" aria-label="Добавить клиента" onClick={() => { setEditing(null); setOpen(true); }}>
              <Plus className="size-4" /> <span className="hidden sm:inline">Добавить клиента</span>
            </Button>
          )}
        </div>
      )}>
      {/* Общая задолженность — как в кассовых системах: одна цифра, красным. */}
      <div className="mb-5 flex flex-wrap gap-3">
        {(["KZT", "USD"] as const).map((currency) => (
          <div key={currency} className="inline-flex min-w-56 flex-col gap-1 rounded-xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-card">
            <span className="text-[13px] font-medium text-[var(--muted-foreground)]">Общая задолженность · {currency}</span>
            <span className="text-[26px] font-bold leading-none tracking-tight tabular-nums text-[var(--destructive)]">
              {formatMoney(debtByCurrency[currency] ?? 0)} {currencySymbol(currency)}
            </span>
          </div>
        ))}
      </div>

      {/* Фильтры — отдельные поля, как в референсе. */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по имени"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Input className="w-full sm:w-44" placeholder="ИИН/БИН" inputMode="numeric"
          value={iinQ} onChange={(e) => setIinQ(e.target.value)} />
        <Input className="w-full sm:w-48" placeholder="+7 (XXX) XXX-XXXX" inputMode="tel"
          value={phoneQ} onChange={(e) => setPhoneQ(e.target.value)} />
      </div>

      {error && !clients && <div className="mb-4"><ErrorAlert message={error} onRetry={reload} /></div>}

      {/* Мобильные карточки: таблица на телефоне нечитаемая. */}
      <div className="flex flex-col gap-3 md:hidden">
        {sorted.length === 0 ? (
          <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Здесь пусто</p>
        ) : sorted.map((c) => (
          <div key={c.id}
            onClick={canMoney ? () => router.push(`/clients/${c.id}`) : undefined}
            className={cn("flex flex-col gap-2.5 rounded-xl border bg-[var(--card)] p-4 shadow-card",
              canMoney && "cursor-pointer")}>
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-sm font-semibold">{c.name}</div>
                <a href={`tel:${c.phone}`} onClick={(e) => e.stopPropagation()}
                  className="flex items-center gap-1.5 text-sm text-[var(--muted-foreground)]">
                  <Phone className="size-3.5" /> {c.phone}
                </a>
              </div>
              <div onClick={(e) => e.stopPropagation()}>
                <RowMenu items={rowMenu(c)} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Задолженность</div>
                {Number(c.debt_total ?? 0) > 0
                  ? <div className="font-medium tabular-nums text-[var(--destructive)]">{formatMoney(c.debt_total!)} {currencySymbol(c.currency)}</div>
                  : <div className="text-[var(--muted-foreground)]">—</div>}
              </div>
              <div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Дата</div>
                <div className="tabular-nums">{c.created_at ? formatDateTime(c.created_at) : "—"}</div>
              </div>
            </div>
          </div>
        ))}
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
                  <SortableHeader label="Сумма задолженностей" sortKey="debt" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <SortableHeader label="Дата" sortKey="created" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                  <TH></TH>
                </TR>
              </THead>
              <TBody>
                {sorted.map((c) => (
                  <TR key={c.id}
                    onClick={canMoney ? () => router.push(`/clients/${c.id}`) : undefined}
                    className={canMoney ? "cursor-pointer hover:bg-[var(--muted)]/40" : ""}>
                    <TD>
                      {canMoney ? (
                        <Link href={`/clients/${c.id}`} onClick={(e) => e.stopPropagation()}
                          className="font-medium text-[var(--ring)] hover:underline">
                          {c.name}
                        </Link>
                      ) : (
                        <span className="font-medium">{c.name}</span>
                      )}
                    </TD>
                    <TD className="tabular-nums">{c.iin || "—"}</TD>
                    <TD className="tabular-nums">{c.phone}</TD>
                    <TD className="tabular-nums">
                      {Number(c.debt_total ?? 0) > 0
                        ? <span className="font-medium text-[var(--destructive)]">{formatMoney(c.debt_total!)} {currencySymbol(c.currency)}</span>
                        : <span className="text-[var(--muted-foreground)]">—</span>}
                    </TD>
                    <TD className="tabular-nums text-[var(--muted-foreground)]">
                      {c.created_at ? formatDateTime(c.created_at) : "—"}
                    </TD>
                    <TD onClick={(e) => e.stopPropagation()}>
                      <div className="flex justify-end">
                        <RowMenu items={rowMenu(c)} />
                      </div>
                    </TD>
                  </TR>
                ))}
                {sorted.length === 0 && (
                  <TR><TD colSpan={6} className="py-14 text-center text-[var(--muted-foreground)]">
                    Здесь пусто</TD></TR>
                )}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow={editing ? "Работа · Изменение" : "Работа · Клиент"}
        title={editing ? "Изменить клиента" : "Новый клиент"}
        description="Контакты и платёжные реквизиты клиента."
        className="max-w-xl">
        {open && (
          <ClientForm
            editing={editing}
            onCancel={() => setOpen(false)}
            onDone={() => { setOpen(false); reload(); }}
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
      <StatementExportModal
        open={statementOpen}
        onClose={() => setStatementOpen(false)}
        endpoint="/clients/statement/"
        filename="clients-full-statement.xlsx"
        title="Общая выписка по клиентам"
        description="Единый Excel-файл по всей клиентской базе, заказам, продажам, оплатам и задолженности."
        scopeLabel="Все клиенты и все финансовые движения"
        sheetsLabel="7 листов: сводка, клиенты, операции, заказы, позиции, платежи и текущие долги."
      />
    </AppShell>
  );
}

export default function ClientsPage() {
  return <RequirePerm perm="clients.view" title="Клиенты"><ClientsPageInner /></RequirePerm>;
}
