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
import { Plus } from "lucide-react";
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

function ClientForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [serverError, setServerError] = useState("");
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      first_name: "", last_name: "", phone: "", country: "",
      iin: "", bank: "", bank_account: "",
    },
  });

  async function onSubmit(values: FormValues) {
    setServerError("");
    try {
      await api.post("/clients/", values);
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

        <div className="sm:col-span-2 mt-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
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
  const [open, setOpen] = useState(false);

  return (
    <AppShell title="Клиенты" section="Работа" description="Справочник клиентов: контакты, страна и платёжные реквизиты.">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{clients?.length ?? 0} клиентов</p>
        <Button size="sm" onClick={() => setOpen(true)}>
          <Plus className="size-4" /> Добавить клиента
        </Button>
      </div>

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead><TR><TH>Имя</TH><TH>Телефон</TH><TH>Страна</TH></TR></THead>
            <TBody>
              {(clients ?? []).map((c) => (
                <TR key={c.id}>
                  <TD className="font-medium">{c.name}</TD>
                  <TD className="tabular-nums">{c.phone}</TD>
                  <TD>{c.country || "—"}</TD>
                </TR>
              ))}
              {(clients ?? []).length === 0 && (
                <TR><TD colSpan={3} className="py-4 text-center text-[var(--muted-foreground)]">
                  Клиентов пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)} title="Новый клиент" className="max-w-xl">
        {open && (
          <ClientForm
            onCancel={() => setOpen(false)}
            onDone={() => { setOpen(false); reload(); }}
          />
        )}
      </Modal>
    </AppShell>
  );
}
