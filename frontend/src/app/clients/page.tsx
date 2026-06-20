"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { Plus } from "lucide-react";
import type { Client } from "@/lib/types";

export default function ClientsPage() {
  const { data: clients, reload } = useApi<Client[]>("/clients/");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", contact: "", country: "", requisites: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      await api.post("/clients/", form);
      setForm({ name: "", contact: "", country: "", requisites: "" });
      setOpen(false); reload();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  return (
    <AppShell title="Клиенты">
      <div className="mb-4 flex justify-between">
        <p className="text-sm text-[var(--muted-foreground)]">{clients?.length ?? 0} клиентов</p>
        <Button size="sm" onClick={() => setOpen(!open)}><Plus className="size-4" /> Добавить</Button>
      </div>
      {open && (
        <Card className="mb-6">
          <CardHeader><CardTitle>Новый клиент</CardTitle></CardHeader>
          <CardContent>
            <form onSubmit={submit} className="grid grid-cols-2 gap-4">
              <div className="flex flex-col gap-1.5">
                <Label>Название*</Label>
                <Input value={form.name} required
                  onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Контакт*</Label>
                <Input value={form.contact} required
                  onChange={(e) => setForm({ ...form, contact: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Страна</Label>
                <Input value={form.country}
                  onChange={(e) => setForm({ ...form, country: e.target.value })} />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label>Реквизиты</Label>
                <Input value={form.requisites}
                  onChange={(e) => setForm({ ...form, requisites: e.target.value })} />
              </div>
              {error && <p className="col-span-2 text-sm text-[var(--destructive)]">{error}</p>}
              <div className="col-span-2">
                <Button type="submit" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}
      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead><TR><TH>Название</TH><TH>Контакт</TH><TH>Страна</TH></TR></THead>
            <TBody>
              {(clients ?? []).map((c) => (
                <TR key={c.id}>
                  <TD className="font-medium">{c.name}</TD>
                  <TD>{c.contact}</TD>
                  <TD>{c.country || "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </CardContent>
      </Card>
    </AppShell>
  );
}
