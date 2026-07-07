"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { StatCard } from "@/components/ui/stat-card";
import { useApi } from "@/lib/use-api";
import { useAuth } from "@/store/auth";
import { api, apiError } from "@/lib/api";
import { can } from "@/lib/can";
import { formatMoney, formatPhone } from "@/lib/utils";
import { Pencil, Phone, Plus, Search, UserRound } from "lucide-react";
import type { Client } from "@/lib/types";

function CityClientsInner() {
  const { data: clients, loading, reload } = useApi<Client[]>("/clients/?department=field");
  const { me } = useAuth();
  const canCreate = can(me, "dept2.create");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  const [q, setQ] = useState("");

  const list = clients ?? [];
  const debtors = list.filter((c) => Number(c.debt_total ?? 0) > 0);
  const filtered = list.filter((c) =>
    !q || `${c.name} ${c.phone}`.toLowerCase().includes(q.toLowerCase()));

  return (
    <AppShell title="Клиенты Сити" section="Отдел «Сити»"
      description="Клиенты выездного отдела. Новые клиенты автоматически попадают в отдел «Сити»."
      actions={canCreate ? (
        <Button size="sm" onClick={() => { setEditing(null); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить клиента</span>
        </Button>
      ) : undefined}>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <StatCard label="Клиентов" value={String(list.length)} />
        <StatCard label="С долгом" value={String(debtors.length)} />
      </section>

      <div className="mb-4 relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-[var(--muted-foreground)]" />
        <Input className="pl-9" placeholder="Поиск по имени или телефону"
          value={q} onChange={(e) => setQ(e.target.value)} />
      </div>

      {loading ? (
        <p className="py-6 text-center text-sm text-[var(--muted-foreground)]">Загрузка…</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((c) => (
            <div key={c.id} className="flex flex-col gap-2 rounded-xl border bg-[var(--card)] p-4 shadow-card">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="flex size-8 items-center justify-center rounded-full bg-[var(--muted)]">
                    <UserRound className="size-4 text-[var(--muted-foreground)]" />
                  </span>
                  <div>
                    <div className="text-sm font-semibold">{c.name}</div>
                    {c.manager_name && (
                      <div className="text-[11px] text-[var(--muted-foreground)]">
                        Менеджер: {c.manager_name}
                      </div>
                    )}
                  </div>
                </div>
                {canCreate && (
                  <Button size="sm" variant="ghost" title="Изменить"
                    onClick={() => { setEditing(c); setOpen(true); }}>
                    <Pencil className="size-4" />
                  </Button>
                )}
              </div>
              <div className="flex items-center justify-between text-sm">
                <a href={`tel:${c.phone}`}
                  className="flex items-center gap-1.5 text-[var(--muted-foreground)] hover:underline">
                  <Phone className="size-3.5" /> {c.phone}
                </a>
                {Number(c.debt_total ?? 0) > 0 ? (
                  <span className="font-medium tabular-nums text-[var(--destructive)]">
                    Долг {formatMoney(c.debt_total!)} ₸
                  </span>
                ) : (
                  <span className="text-[var(--muted-foreground)]">Без долга</span>
                )}
              </div>
            </div>
          ))}
          {filtered.length === 0 && (
            <p className="py-6 text-center text-sm text-[var(--muted-foreground)] sm:col-span-2 lg:col-span-3">
              Клиентов пока нет.
            </p>
          )}
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow="Отдел «Сити» · Клиент"
        title={editing ? "Изменить клиента" : "Новый клиент"}
        description="Клиент будет закреплён за вами в отделе «Сити»."
        className="max-w-md">
        {open && (
          <CityClientForm editing={editing}
            onCancel={() => setOpen(false)}
            onDone={() => { setOpen(false); reload(); }} />
        )}
      </Modal>
    </AppShell>
  );
}

function CityClientForm({ editing, onCancel, onDone }: {
  editing: Client | null; onCancel: () => void; onDone: () => void;
}) {
  const [firstName, setFirstName] = useState(editing?.first_name ?? "");
  const [lastName, setLastName] = useState(editing?.last_name ?? "");
  const [phone, setPhone] = useState(editing?.phone ?? "");
  const [iin, setIin] = useState(editing?.iin ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try {
      const payload = {
        first_name: firstName, last_name: lastName, phone, iin,
        department: "field",
      };
      if (editing) await api.patch(`/clients/${editing.id}/`, payload);
      else await api.post("/clients/", payload);
      onDone();
    } catch (err) { setError(apiError(err)); }
    finally { setBusy(false); }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <Label>Имя</Label>
          <Input value={firstName} autoFocus required minLength={2}
            onChange={(e) => setFirstName(e.target.value)} placeholder="Иван" />
        </div>
        <div className="grid gap-2">
          <Label>Фамилия</Label>
          <Input value={lastName} required minLength={2}
            onChange={(e) => setLastName(e.target.value)} placeholder="Петров" />
        </div>
      </div>
      <div className="grid gap-2">
        <Label>Номер телефона</Label>
        <Input type="tel" inputMode="tel" placeholder="+7 (___) ___-__-__" value={phone}
          onChange={(e) => setPhone(formatPhone(e.target.value))} required />
      </div>
      <div className="grid gap-2">
        <Label>ИИН / БИН (необязательно)</Label>
        <Input inputMode="numeric" placeholder="12 цифр" maxLength={12} value={iin}
          onChange={(e) => setIin(e.target.value.replace(/\D/g, "").slice(0, 12))} />
      </div>
      {error && <p className="text-sm text-[var(--destructive)]">{error}</p>}
      <div className="flex justify-end gap-2 border-t pt-4">
        <Button type="button" variant="outline" onClick={onCancel}>Отмена</Button>
        <Button type="submit" disabled={busy}>
          {busy ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
    </form>
  );
}

export default function CityClientsPage() {
  return <RequirePerm perm={["dept2.view", "dept2.view_all"]} title="Клиенты Сити"><CityClientsInner /></RequirePerm>;
}
