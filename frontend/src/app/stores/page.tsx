"use client";
import { useState } from "react";
import { AppShell } from "@/components/layout/app-shell";
import { RequirePerm } from "@/components/require-perm";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Modal } from "@/components/ui/modal";
import { Table, THead, TBody, TR, TH, TD } from "@/components/ui/table";
import { StatCard } from "@/components/ui/stat-card";
import { ErrorAlert } from "@/components/ui/data-state";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatPhone } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { Plus, Pencil, Trash2 } from "lucide-react";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { Store, Client } from "@/lib/types";

const SCHEDULE_LABELS: Record<string, string> = {
  none: "Без расписания",
  monthly: "По числам месяца",
  weekly: "По дням недели",
};
const WEEKDAYS = [
  { v: 1, label: "Пн" }, { v: 2, label: "Вт" }, { v: 3, label: "Ср" },
  { v: 4, label: "Чт" }, { v: 5, label: "Пт" }, { v: 6, label: "Сб" }, { v: 7, label: "Вс" },
];

function describeSchedule(s: Store): string {
  if (s.payment_schedule_type === "none") return "—";
  const days = s.payment_days ?? [];
  if (s.payment_schedule_type === "monthly")
    return days.length ? `Числа: ${days.join(", ")}` : "Числа не заданы";
  return days.length
    ? `Дни: ${days.map((d) => WEEKDAYS.find((w) => w.v === d)?.label ?? d).join(", ")}`
    : "Дни не заданы";
}

function StoreForm({ clients, editing, onDone, onCancel }: {
  clients: Client[]; editing?: Store | null; onDone: () => void; onCancel: () => void;
}) {
  const [client, setClient] = useState<string>(editing ? String(editing.client) : "");
  const [name, setName] = useState(editing?.name ?? "");
  const [address, setAddress] = useState(editing?.address ?? "");
  const [phone, setPhone] = useState(editing?.phone ?? "");
  const [scheduleType, setScheduleType] = useState(editing?.payment_schedule_type ?? "none");
  const [days, setDays] = useState<number[]>(editing?.payment_days ?? []);
  const [monthlyInput, setMonthlyInput] = useState(
    editing?.payment_schedule_type === "monthly" ? (editing.payment_days ?? []).join(", ") : ""
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function toggleWeekday(v: number) {
    setDays((d) => d.includes(v) ? d.filter((x) => x !== v) : [...d, v].sort((a, b) => a - b));
  }

  function parseMonthly(s: string): number[] {
    return Array.from(new Set(
      s.split(/[,\s]+/).map((x) => parseInt(x, 10))
        .filter((n) => Number.isInteger(n) && n >= 1 && n <= 31)
    )).sort((a, b) => a - b);
  }

  async function submit() {
    setBusy(true); setError("");
    const payment_days = scheduleType === "monthly"
      ? parseMonthly(monthlyInput)
      : scheduleType === "weekly" ? days : [];
    const payload = {
      client: Number(client), name, address, phone,
      payment_schedule_type: scheduleType, payment_days,
    };
    try {
      if (editing) await api.patch(`/stores/${editing.id}/`, payload);
      else await api.post("/stores/", payload);
      onDone();
    } catch (e) { setError(apiError(e)); } finally { setBusy(false); }
  }

  const valid = client && name.trim().length >= 2;

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label>Клиент-владелец</Label>
          <Select value={client} onValueChange={setClient}>
            <SelectTrigger><SelectValue placeholder="Выберите клиента" /></SelectTrigger>
            <SelectContent>
              {clients.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Название магазина</Label>
          <Input autoFocus placeholder="Магазин №1" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Адрес</Label>
          <Input placeholder="Адрес" value={address} onChange={(e) => setAddress(e.target.value)} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label>Телефон</Label>
          <Input type="tel" placeholder="+7 (___) ___-__-__" value={phone}
            onChange={(e) => setPhone(formatPhone(e.target.value))} />
        </div>
      </div>

      <div className="border-t pt-4">
        <div className="mb-3 text-[12px] font-medium text-[var(--muted-foreground)]">Расписание оплат</div>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label>Тип</Label>
            <Select value={scheduleType} onValueChange={(v) => setScheduleType(v as Store["payment_schedule_type"])}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(SCHEDULE_LABELS).map(([v, l]) => <SelectItem key={v} value={v}>{l}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          {scheduleType === "monthly" && (
            <div className="flex flex-col gap-1.5">
              <Label>Числа месяца (через запятую)</Label>
              <Input placeholder="напр. 5, 20" value={monthlyInput}
                onChange={(e) => setMonthlyInput(e.target.value)} />
            </div>
          )}
          {scheduleType === "weekly" && (
            <div className="flex flex-col gap-1.5 sm:col-span-1">
              <Label>Дни недели</Label>
              <div className="flex flex-wrap gap-1.5">
                {WEEKDAYS.map((w) => (
                  <button key={w.v} type="button" onClick={() => toggleWeekday(w.v)}
                    className={cn("rounded-md border px-3 py-1.5 text-sm transition-colors",
                      days.includes(w.v)
                        ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]"
                        : "hover:bg-[var(--muted)]")}>
                    {w.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        {scheduleType !== "none" && (
          <p className="mt-3 text-xs text-[var(--muted-foreground)]">
            Магазин сможет гасить долг только в эти дни. Вне окна оплата блокируется.
          </p>
        )}
      </div>

      {error && (
        <p className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]">
          {error}
        </p>
      )}

      <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28" onClick={onCancel}>Отмена</Button>
        <Button type="button" className="w-full sm:w-auto sm:min-w-28" disabled={!valid || busy} onClick={submit}>
          {busy ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
    </div>
  );
}

function StoresPageInner() {
  const { data: stores, error, reload } = useApi<Store[]>("/stores/");
  const { data: clients } = useApi<Client[]>("/clients/");
  const { me } = useAuth();
  const canCreate = can(me, "clients.create");
  const canEdit = can(me, "clients.edit");
  const canDelete = can(me, "clients.delete");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Store | null>(null);
  const [delItem, setDelItem] = useState<Store | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);

  const list = stores ?? [];
  const clientName = (id: number) => (clients ?? []).find((c) => c.id === id)?.name ?? `#${id}`;

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true); setDelError("");
    try { await api.delete(`/stores/${delItem.id}/`); setDelItem(null); reload(); }
    catch (e) { setDelError(apiError(e)); } finally { setDelBusy(false); }
  }

  return (
    <AppShell title="Магазины" section="Работа" description="Магазины клиентов и их расписание оплат."
      actions={canCreate &&
        <Button size="sm" aria-label="Добавить магазин" onClick={() => { setEditing(null); setOpen(true); }}>
          <Plus className="size-4" /> <span className="hidden sm:inline">Добавить магазин</span>
        </Button>
      }>
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Всего магазинов" value={String(list.length)} />
      </section>

      {error && !stores && <div className="mb-4"><ErrorAlert message={error} onRetry={reload} /></div>}

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR><TH>Магазин</TH><TH>Клиент</TH><TH>Расписание</TH><TH></TH></TR>
            </THead>
            <TBody>
              {list.map((s) => (
                <TR key={s.id}>
                  <TD className="font-medium">{s.name}
                    {s.phone && <span className="block text-xs text-[var(--muted-foreground)]">{s.phone}</span>}
                  </TD>
                  <TD>{clientName(s.client)}</TD>
                  <TD>
                    <div className="flex items-center gap-2">
                      <Badge tone={s.payment_schedule_type === "none" ? "muted" : "primary"}>
                        {SCHEDULE_LABELS[s.payment_schedule_type]}
                      </Badge>
                      <span className="text-xs text-[var(--muted-foreground)]">{describeSchedule(s)}</span>
                    </div>
                  </TD>
                  <TD>
                    <div className="flex items-center justify-end gap-1">
                      {canEdit && (
                        <Button size="sm" variant="ghost" onClick={() => { setEditing(s); setOpen(true); }} title="Изменить">
                          <Pencil className="size-4" />
                        </Button>
                      )}
                      {canDelete && (
                        <Button size="sm" variant="ghost"
                          className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                          onClick={() => { setDelError(""); setDelItem(s); }} title="Удалить">
                          <Trash2 className="size-4" />
                        </Button>
                      )}
                    </div>
                  </TD>
                </TR>
              ))}
              {list.length === 0 && (
                <TR><TD colSpan={4} className="py-4 text-center text-[var(--muted-foreground)]">
                  Магазинов пока нет.</TD></TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal open={open} onClose={() => setOpen(false)}
        eyebrow={editing ? "Работа · Изменение" : "Работа · Магазин"}
        title={editing ? "Изменить магазин" : "Новый магазин"}
        description="Магазин принадлежит клиенту; операционист задаёт дни оплаты."
        className="max-w-xl">
        {open && (
          <StoreForm clients={clients ?? []} editing={editing}
            onCancel={() => setOpen(false)}
            onDone={() => { setOpen(false); reload(); }} />
        )}
      </Modal>

      <ConfirmDialog open={!!delItem} onClose={() => setDelItem(null)}
        title="Удалить магазин?"
        description={delItem ? `«${delItem.name}» будет удалён. Действие необратимо.` : ""}
        busy={delBusy} error={delError} onConfirm={confirmDelete} />
    </AppShell>
  );
}

export default function StoresPage() {
  return <RequirePerm perm="clients.view" title="Магазины"><StoresPageInner /></RequirePerm>;
}
