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
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select-ui";
import { useApi } from "@/lib/use-api";
import { api, apiError } from "@/lib/api";
import { formatPhone } from "@/lib/utils";
import { cn } from "@/lib/utils";
import { Plus, Pencil, Trash2 } from "lucide-react";
import { useAuth } from "@/store/auth";
import { can } from "@/lib/can";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { Store } from "@/lib/types";
import { formatPaymentSchedule, validatePaymentSchedule } from "./schedule-validation";

const SCHEDULE_LABELS: Record<string, string> = {
  none: "Без расписания",
  monthly: "По числам месяца",
  weekly: "По дням недели",
};
const WEEKDAYS = [
  { v: 1, label: "Пн" },
  { v: 2, label: "Вт" },
  { v: 3, label: "Ср" },
  { v: 4, label: "Чт" },
  { v: 5, label: "Пт" },
  { v: 6, label: "Сб" },
  { v: 7, label: "Вс" },
];

interface ClientPickerItem {
  id: number;
  name: string;
}

function StoreForm({
  clients,
  editing,
  onDone,
  onCancel,
}: {
  clients: ClientPickerItem[];
  editing?: Store | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [client, setClient] = useState<string>(editing ? String(editing.client) : "");
  const [name, setName] = useState(editing?.name ?? "");
  const [address, setAddress] = useState(editing?.address ?? "");
  const [phone, setPhone] = useState(editing?.phone ?? "");
  const [scheduleType, setScheduleType] = useState(editing?.payment_schedule_type ?? "none");
  const [days, setDays] = useState<number[]>(
    editing?.payment_schedule_type === "weekly"
      ? (editing.payment_days ?? []).filter((day) => Number.isInteger(day) && day >= 1 && day <= 7)
      : [],
  );
  const [monthlyInput, setMonthlyInput] = useState(
    editing?.payment_schedule_type === "monthly" ? (editing.payment_days ?? []).join(", ") : "",
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function toggleWeekday(v: number) {
    setError("");
    setDays((d) => (d.includes(v) ? d.filter((x) => x !== v) : [...d, v].sort((a, b) => a - b)));
  }

  const scheduleValidation = validatePaymentSchedule(scheduleType, monthlyInput, days);

  async function submit() {
    setError("");
    if (!scheduleValidation.ok) {
      setError(scheduleValidation.message);
      return;
    }

    setBusy(true);
    const payload = {
      client: Number(client),
      name,
      address,
      phone,
      payment_schedule_type: scheduleType,
      payment_days: scheduleValidation.days,
    };
    try {
      if (editing) await api.patch(`/stores/${editing.id}/`, payload);
      else await api.post("/stores/", payload);
      onDone();
    } catch (e) {
      setError(apiError(e));
    } finally {
      setBusy(false);
    }
  }

  const valid = Boolean(client) && name.trim().length >= 2 && scheduleValidation.ok;

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="store-client">Клиент-владелец</Label>
          <Select value={client} onValueChange={setClient}>
            <SelectTrigger id="store-client">
              <SelectValue placeholder="Выберите клиента" />
            </SelectTrigger>
            <SelectContent>
              {clients.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="store-name">Название магазина</Label>
          <Input
            id="store-name"
            autoFocus
            placeholder="Магазин №1"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="store-address">Адрес</Label>
          <Input
            id="store-address"
            autoComplete="street-address"
            placeholder="Адрес"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="store-phone">Телефон</Label>
          <Input
            id="store-phone"
            type="tel"
            autoComplete="tel"
            placeholder="+7 (___) ___-__-__"
            value={phone}
            onChange={(e) => setPhone(formatPhone(e.target.value))}
          />
        </div>
      </div>

      <div className="border-t pt-4">
        <div className="mb-3 text-[12px] font-medium text-[var(--muted-foreground)]">Расписание оплат</div>
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="store-schedule">Тип</Label>
            <Select
              value={scheduleType}
              onValueChange={(v) => {
                setError("");
                setScheduleType(v as Store["payment_schedule_type"]);
              }}
            >
              <SelectTrigger id="store-schedule">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(SCHEDULE_LABELS).map(([v, l]) => (
                  <SelectItem key={v} value={v}>
                    {l}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {scheduleType === "monthly" && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="store-payment-days">Числа месяца (через запятую)</Label>
              <Input
                id="store-payment-days"
                placeholder="напр. 5, 20"
                value={monthlyInput}
                onChange={(e) => {
                  setError("");
                  setMonthlyInput(e.target.value);
                }}
                aria-invalid={!scheduleValidation.ok}
                aria-describedby={!scheduleValidation.ok ? "store-schedule-error" : undefined}
              />
            </div>
          )}
          {scheduleType === "weekly" && (
            <div className="flex flex-col gap-1.5 sm:col-span-1">
              <Label id="store-weekdays-label">Дни недели</Label>
              <div
                className="flex flex-wrap gap-1.5"
                role="group"
                aria-labelledby="store-weekdays-label"
                aria-describedby={!scheduleValidation.ok ? "store-schedule-error" : undefined}
              >
                {WEEKDAYS.map((w) => (
                  <button
                    key={w.v}
                    type="button"
                    onClick={() => toggleWeekday(w.v)}
                    aria-pressed={days.includes(w.v)}
                    className={cn(
                      "rounded-md border px-3 py-1.5 text-sm transition-colors",
                      days.includes(w.v)
                        ? "border-[var(--primary)] bg-[var(--primary)] text-[var(--primary-foreground)]"
                        : "hover:bg-[var(--muted)]",
                    )}
                  >
                    {w.label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
        {scheduleType !== "none" && (
          <>
            {!scheduleValidation.ok && (
              <p id="store-schedule-error" role="alert" className="mt-3 text-xs text-[var(--destructive)]">
                {scheduleValidation.message}
              </p>
            )}
            <p className="mt-3 text-xs text-[var(--muted-foreground)]">
              Магазин сможет гасить долг только в эти дни. Вне окна оплата блокируется.
            </p>
          </>
        )}
      </div>

      {error && (
        <p
          role="alert"
          className="rounded-md border border-[var(--destructive)]/20 bg-[var(--destructive)]/10 px-3 py-2 text-sm text-[var(--destructive)]"
        >
          {error}
        </p>
      )}

      <div className="flex flex-col-reverse gap-2 border-t pt-5 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" className="w-full sm:w-auto sm:min-w-28" onClick={onCancel}>
          Отмена
        </Button>
        <Button type="button" className="w-full sm:w-auto sm:min-w-28" disabled={!valid || busy} onClick={submit}>
          {busy ? "Сохранение…" : "Сохранить"}
        </Button>
      </div>
    </div>
  );
}

function StoresPageInner() {
  const { data: stores, error, reload } = useApi<Store[]>("/stores/");
  const { me } = useAuth();
  const canCreate = can(me, "clients.create");
  const canEdit = can(me, "clients.edit");
  const canDelete = can(me, "clients.delete");
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Store | null>(null);
  const [delItem, setDelItem] = useState<Store | null>(null);
  const [delError, setDelError] = useState("");
  const [delBusy, setDelBusy] = useState(false);
  const {
    data: clients,
    loading: clientsLoading,
    error: clientsError,
    reload: reloadClients,
  } = useApi<ClientPickerItem[]>(open ? "/clients/picker/" : null);

  const list = stores ?? [];

  async function confirmDelete() {
    if (!delItem) return;
    setDelBusy(true);
    setDelError("");
    try {
      await api.delete(`/stores/${delItem.id}/`);
      setDelItem(null);
      reload();
    } catch (e) {
      setDelError(apiError(e));
    } finally {
      setDelBusy(false);
    }
  }

  return (
    <AppShell
      title="Магазины"
      section="Работа"
      description="Магазины клиентов и их расписание оплат."
      actions={
        canCreate && (
          <Button
            size="sm"
            aria-label="Добавить магазин"
            onClick={() => {
              setEditing(null);
              setOpen(true);
            }}
          >
            <Plus className="size-4" /> <span className="hidden sm:inline">Добавить магазин</span>
          </Button>
        )
      }
    >
      <section className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatCard label="Всего магазинов" value={String(list.length)} />
      </section>

      {error && !stores && (
        <div className="mb-4">
          <ErrorAlert message={error} onRetry={reload} />
        </div>
      )}

      <Card>
        <CardContent className="pt-6">
          <Table>
            <THead>
              <TR>
                <TH>Магазин</TH>
                <TH>Клиент</TH>
                <TH>Расписание</TH>
                <TH></TH>
              </TR>
            </THead>
            <TBody>
              {list.map((s) => (
                <TR key={s.id}>
                  <TD className="font-medium">
                    {s.name}
                    {s.phone && <span className="block text-xs text-[var(--muted-foreground)]">{s.phone}</span>}
                  </TD>
                  <TD>{s.client_name ?? `#${s.client}`}</TD>
                  <TD>
                    <div className="flex items-center gap-2">
                      <Badge tone={s.payment_schedule_type === "none" ? "muted" : "primary"}>
                        {SCHEDULE_LABELS[s.payment_schedule_type]}
                      </Badge>
                      <span className="text-xs text-[var(--muted-foreground)]">{formatPaymentSchedule(s)}</span>
                    </div>
                  </TD>
                  <TD>
                    <div className="flex items-center justify-end gap-1">
                      {canEdit && (
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            setEditing(s);
                            setOpen(true);
                          }}
                          title="Изменить"
                        >
                          <Pencil className="size-4" />
                        </Button>
                      )}
                      {canDelete && (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-[var(--muted-foreground)] hover:text-[var(--destructive)]"
                          onClick={() => {
                            setDelError("");
                            setDelItem(s);
                          }}
                          title="Удалить"
                        >
                          <Trash2 className="size-4" />
                        </Button>
                      )}
                    </div>
                  </TD>
                </TR>
              ))}
              {list.length === 0 && (
                <TR>
                  <TD colSpan={4} className="py-4 text-center text-[var(--muted-foreground)]">
                    Магазинов пока нет.
                  </TD>
                </TR>
              )}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow={editing ? "Работа · Изменение" : "Работа · Магазин"}
        title={editing ? "Изменить магазин" : "Новый магазин"}
        description="Магазин принадлежит клиенту; операционист задаёт дни оплаты."
        className="max-w-xl"
      >
        {open && clientsError ? (
          <ErrorAlert message={clientsError} onRetry={() => void reloadClients()} />
        ) : open && (clientsLoading || !clients) ? (
          <p className="py-8 text-center text-sm text-[var(--muted-foreground)]">Загрузка клиентов…</p>
        ) : open ? (
          <StoreForm
            clients={clients ?? []}
            editing={editing}
            onCancel={() => setOpen(false)}
            onDone={() => {
              setOpen(false);
              reload();
            }}
          />
        ) : null}
      </Modal>

      <ConfirmDialog
        open={!!delItem}
        onClose={() => setDelItem(null)}
        title="Удалить магазин?"
        description={delItem ? `«${delItem.name}» будет удалён. Действие необратимо.` : ""}
        busy={delBusy}
        error={delError}
        onConfirm={confirmDelete}
      />
    </AppShell>
  );
}

export default function StoresPage() {
  return (
    <RequirePerm perm="clients.view" title="Магазины">
      <StoresPageInner />
    </RequirePerm>
  );
}
