"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { Plus } from "lucide-react";
import type { Client } from "@/lib/types";

export default function ClientsPage() {
  const { data: clients, reload } = useApi<Client[]>("/clients/");
  const empty = { first_name: "", last_name: "", phone: "", country: "", requisites: "" };
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(empty);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/clients/", form);
      setForm(empty);
      setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Клиенты">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{clients?.length ?? 0} клиентов</p>
        <Button size="sm" onClick={() => { setError(""); setOpen(true); }}>
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
                  <TD>{c.phone}</TD>
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
        <form onSubmit={submit} className="grid grid-cols-1 gap-x-5 gap-y-5 sm:grid-cols-2">
          <div className="grid gap-2">
            <Label>Имя*</Label>
            <Input value={form.first_name} required autoFocus
              onChange={(e) => setForm({ ...form, first_name: e.target.value })} />
          </div>
          <div className="grid gap-2">
            <Label>Фамилия*</Label>
            <Input value={form.last_name} required
              onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
          </div>
          <div className="grid gap-2">
            <Label>Номер телефона*</Label>
            <Input type="tel" value={form.phone} required placeholder="+7 …"
              onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          </div>
          <div className="grid gap-2">
            <Label>Страна</Label>
            <Input value={form.country}
              onChange={(e) => setForm({ ...form, country: e.target.value })} />
          </div>
          <div className="grid gap-2 sm:col-span-2">
            <Label>Реквизиты</Label>
            <Input value={form.requisites}
              onChange={(e) => setForm({ ...form, requisites: e.target.value })} />
          </div>
          {error && (
            <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)] sm:col-span-2">
              {error}
            </p>
          )}
          <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:col-span-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28" onClick={() => setOpen(false)}>Отмена</Button>
            <Button type="submit" className="w-full sm:w-auto sm:min-w-28" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</Button>
          </div>
        </form>
      </Modal>
    </AppShell>
  );
}
