"use client";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { SortableHeader, type SortDir } from "@/components/ui/sortable-header";
import { Search } from "lucide-react";
import {
  Form, FormField, FormItem, FormLabel, FormControl, FormMessage,
} from "@/components/ui/form";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatPhone } from "@/lib/utils";
import { COUNTRIES } from "@/lib/countries";
import { Plus, Pencil, Trash2 } from "lucide-react";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { Client } from "@/lib/types";

const schema = z.object({
  first_name: z.string().min(2, "Введите имя (мин. 2 символа)"),
  last_name: z.string().min(2, "Введите фамилию (мин. 2 символа)"),
  phone: z
    .string()
    .refine((v) => v.replace(/\D/g, "").length === 11, "Введите номер полностью"),
  country: z.string().optional(),
  iin: z.string().optional().refine(
    (v) => !v || /^\d{12}$/.test(v), "ИИН/БИН — 12 цифр"
  ),
  bank: z.string().optional(),
  bank_account: z.string().optional(),
});
type FormValues = z.infer<typeof schema>;

function ClientForm({ onDone, onCancel, editing }: { onDone: () => void; onCancel: () => void; editing?: Client | null }) {
  const [serverError, setServerError] = useState("");
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: editing ? {
      first_name: editing.first_name, last_name: editing.last_name, phone: editing.phone,
      country: editing.country ?? "", iin: editing.iin ?? "",
      bank: editing.bank ?? "", bank_account: editing.bank_account ?? "",
    } : {
      first_name: "", last_name: "", phone: "", country: "",
      iin: "", bank: "", bank_account: "",
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
            <FormLabel>Фамилия</FormLabel>
            <FormControl><Input placeholder="Петров" {...field} /></FormControl>
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

export default function ClientsPage() {
  const { data: clients, reload } = useApi<Client[]>("/clients/");
  const { me } = useAuth();
  const canEdit = can(me, "clients.edit");
  const canDelete = can(me, "clients.delete");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  const [q, setQ] = useState("");
  const [sortKey, setSortKey] = useState("name");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [delItem, setDelItem] = useState<Client | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true); setDelError("");
    try {
      await api.delete(`/clients/${delItem.id}/`);
      setDelItem(null); reload();
    } catch (e) { setDelError(apiError(e)); } finally { setDelBusy(false); }
  }

  const list = clients ?? [];
  const toggleSort = (k: string) => {
    if (k === sortKey) setSortDir(sortDir === "asc" ? "desc" : "asc");
    else { setSortKey(k); setSortDir("asc"); }
  };
  const filtered = list.filter((c) => {
    if (!q) return true;
    return `${c.name} ${c.phone} ${c.country ?? ""}`.toLowerCase().includes(q.toLowerCase());
  });
  const sorted = [...filtered].sort((a, b) => {
    const av = sortKey === "phone" ? a.phone : a.name;
    const bv = sortKey === "phone" ? b.phone : b.name;
    const cmp = String(av).localeCompare(String(bv), "ru");
    return sortDir === "asc" ? cmp : -cmp;
  });

  return (
    <AppShell title="Клиенты" section="Работа" description="Справочник клиентов: контакты, страна и платёжные реквизиты."
      actions={
        <Button size="sm" onClick={() => { setEditing(null); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить клиента</span>
        </Button>
      }>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Всего клиентов" value={String(list.length)} />
      </section>

      <div className="mb-4">
        <div className="relative max-w-md flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
          <Input className="pl-9" placeholder="Поиск по имени, телефону, стране"
            value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <SortableHeader label="Имя" sortKey="name" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <SortableHeader label="Телефон" sortKey="phone" activeKey={sortKey} dir={sortDir} onClick={toggleSort} />
                <TH>Страна</TH><TH></TH>
              </TR>
            </THead>
            <TBody>
              {sorted.map((c) => (
                <TR key={c.id}>
                  <TD className="font-medium">{c.name}</TD>
                  <TD className="tabular-nums">{c.phone}</TD>
                  <TD>{c.country || "—"}</TD>
                  <TD>
                    <div className="flex items-center justify-end gap-1">
                      {canEdit && (
                        <Button size="sm" variant="ghost" onClick={() => { setEditing(c); setOpen(true); }} title="Изменить">
                          <Pencil className="size-4" />
                        </Button>
                      )}
                      {canDelete && (
                        <Button size="sm" variant="ghost"
                          className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                          onClick={() => { setDelError(""); setDelItem(c); }} title="Удалить">
                          <Trash2 className="size-4" />
                        </Button>
                      )}
                    </div>
                  </TD>
                </TR>
              ))}
              {sorted.length === 0 && (
                <TR><TD colSpan={4} className="py-4 text-center text-[var(--muted-foreground)]">
                  Клиентов пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

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
    </AppShell>
  );
}
