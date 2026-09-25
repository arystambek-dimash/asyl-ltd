"use client";
import { useState } from "react";
import { Check, Pencil, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { PasswordInput } from "@/components/ui/password-input";
import { api, apiError } from "@/lib/api";
import { copyText } from "@/lib/clipboard";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";
import type { Department } from "@/lib/types";
import { useAuth } from "@/store/auth";

const DEPARTMENT_COLORS = ["#315FD5", "#D68B2C", "#238C6E", "#B84A5A", "#7654B3", "#3B7F91", "#6B7280"];

/** Модалка «Отделы продаж»: название, цвет, основной/отключён; суперюзеру — ещё ключ Kaspi / ApiPay отдела. */
export function DepartmentManager({ onChanged }: { onChanged: () => void }) {
  const { me } = useAuth();
  const [open, setOpen] = useState(false);
  const { data, reload } = useApi<Department[]>(open ? "/departments/?all=1" : null);
  const [editing, setEditing] = useState<Department | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState(DEPARTMENT_COLORS[0]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  // Блок Kaspi показывает состояние из перечитанного списка: после сохранения ключа editing устарел бы.
  const editingRow = editing ? (data?.find((row) => row.id === editing.id) ?? editing) : null;

  function begin(department?: Department) {
    setEditing(department ?? null);
    setName(department?.name ?? "");
    setColor(department?.color ?? DEPARTMENT_COLORS[(data?.length ?? 0) % DEPARTMENT_COLORS.length]);
    setError("");
  }

  async function run(request: () => Promise<unknown>, after?: () => void) {
    setSaving(true);
    setError("");
    try {
      await request();
      after?.();
      await reload();
      onChanged();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setSaving(false);
    }
  }

  function save() {
    return run(
      () =>
        editing
          ? api.patch(`/departments/${editing.id}/`, { name, color })
          : api.post("/departments/", { name, color }),
      begin,
    );
  }

  function update(department: Department, payload: Partial<Department>) {
    return run(() => api.patch(`/departments/${department.id}/`, payload));
  }

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          setOpen(true);
          begin();
        }}
      >
        <Settings2 className="size-4" /> <span className="hidden sm:inline">Отделы</span>
      </Button>
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        eyebrow="Заказы · Настройка"
        title="Отделы продаж"
        description="Добавляйте отделы здесь — они сразу появятся в новом заказе, фильтрах и аналитике."
        className="max-w-2xl"
      >
        <div className="grid gap-5 md:grid-cols-[1.15fr_.85fr]">
          <div className="flex flex-col gap-2">
            {(data ?? []).map((department) => (
              <div
                key={department.id}
                className={cn(
                  "group flex items-center gap-3 rounded-xl border p-3 transition",
                  department.is_active ? "bg-[var(--card)]" : "bg-[var(--muted)]/35 opacity-65",
                )}
              >
                <span
                  className="size-3 shrink-0 rounded-full ring-4 ring-current/10"
                  style={{ backgroundColor: department.color, color: department.color }}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-semibold">{department.name}</span>
                    {department.is_default && (
                      <span className="rounded-full bg-[var(--muted)] px-2 py-0.5 text-[10px] font-medium text-[var(--muted-foreground)]">
                        основной
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-[var(--muted-foreground)]">
                    {department.order_count} заказов · {department.is_active ? "доступен" : "отключён"} ·{" "}
                    {department.apipay_configured ? "Kaspi подключён" : "Kaspi не подключён"}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => begin(department)}
                  className="rounded-lg p-2 text-[var(--muted-foreground)] hover:bg-[var(--accent)] hover:text-[var(--foreground)]"
                  title="Изменить отдел"
                >
                  <Pencil className="size-3.5" />
                </button>
                <button
                  type="button"
                  disabled={saving || (department.is_default && department.is_active)}
                  onClick={() => void update(department, { is_active: !department.is_active })}
                  className="min-w-20 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium disabled:opacity-40"
                >
                  {department.is_active ? "Отключить" : "Включить"}
                </button>
              </div>
            ))}
            {(data ?? []).length === 0 && (
              <div className="rounded-xl border border-dashed p-8 text-center text-sm text-[var(--muted-foreground)]">
                Создайте первый отдел для новых заказов.
              </div>
            )}
          </div>

          <div className="h-fit rounded-2xl border bg-[var(--muted)]/25 p-4">
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-bold">{editing ? "Изменить отдел" : "Новый отдел"}</div>
                <div className="text-[11px] text-[var(--muted-foreground)]">Название и цвет метки</div>
              </div>
              {editing && (
                <button
                  type="button"
                  onClick={() => begin()}
                  className="text-xs font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
                >
                  Сбросить
                </button>
              )}
            </div>
            <Input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={100}
              placeholder="Например, Оптовые продажи"
            />
            <div className="mt-3 flex flex-wrap gap-2">
              {DEPARTMENT_COLORS.map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setColor(item)}
                  aria-label={`Цвет ${item}`}
                  className={cn(
                    "flex size-8 items-center justify-center rounded-full transition-transform hover:scale-110",
                    color === item && "ring-2 ring-[var(--foreground)] ring-offset-2 ring-offset-[var(--card)]",
                  )}
                  style={{ backgroundColor: item }}
                >
                  {color === item && <Check className="size-4 text-white" />}
                </button>
              ))}
            </div>
            {error && <p className="mt-3 text-sm text-[var(--destructive)]">{error}</p>}
            <Button className="mt-4 w-full" disabled={saving || !name.trim()} onClick={() => void save()}>
              {saving ? "Сохранение…" : editing ? "Сохранить изменения" : "Добавить отдел"}
            </Button>
            {editing && !editing.is_default && (
              <button
                type="button"
                disabled={saving}
                onClick={() => void update(editing, { is_default: true, is_active: true })}
                className="mt-3 w-full text-center text-xs font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
              >
                Сделать основным
              </button>
            )}
            {editingRow && me?.is_superuser && (
              <ApiPayBlock
                key={editingRow.id}
                department={editingRow}
                onSaved={async () => {
                  await reload();
                  onChanged();
                }}
              />
            )}
          </div>
        </div>
      </Modal>
    </>
  );
}

type ApiPayPayload = Partial<Record<"apipay_api_key" | "apipay_webhook_secret", string>>;

/**
 * Ключ ApiPay и секрет вебхука отдела. Сервер их не возвращает, поэтому поля
 * всегда пустые: пустое поле не отправляется, а «Отключить Kaspi» шлёт пустой ключ.
 */
function ApiPayBlock({ department, onSaved }: { department: Department; onSaved: () => Promise<void> }) {
  const [key, setKey] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const webhookUrl = `${typeof window === "undefined" ? "" : window.location.origin}/api/webhooks/apipay/`;
  const status = department.apipay_configured
    ? `Ключ ${department.apipay_key_hint || "••••"} · ${
        department.apipay_webhook_configured ? "вебхук настроен" : "без секрета вебхука"
      }`
    : "Не подключён";

  async function save(payload: ApiPayPayload) {
    setBusy(true);
    setError("");
    try {
      await api.patch(`/departments/${department.id}/`, payload);
      setKey("");
      setSecret("");
      await onSaved();
    } catch (cause) {
      setError(apiError(cause));
    } finally {
      setBusy(false);
    }
  }

  function submit() {
    const payload: ApiPayPayload = {};
    if (key.trim()) payload.apipay_api_key = key.trim();
    if (secret.trim()) payload.apipay_webhook_secret = secret.trim();
    void save(payload);
  }

  return (
    <div className="mt-4 rounded-xl border bg-[var(--card)] p-3">
      <div className="text-sm font-bold">Kaspi / ApiPay</div>
      <div className="text-[11px] text-[var(--muted-foreground)]">{status}</div>
      <label className="mt-3 block text-xs font-medium">
        API-ключ
        <PasswordInput
          aria-label="API-ключ"
          autoComplete="off"
          className="mt-1"
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
      </label>
      <label className="mt-3 block text-xs font-medium">
        Секрет вебхука
        <PasswordInput
          aria-label="Секрет вебхука"
          autoComplete="off"
          className="mt-1"
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
        />
      </label>
      <div className="mt-3 text-[11px] text-[var(--muted-foreground)]">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span>Адрес вебхука:</span>
          <code className="rounded bg-[var(--muted)] px-1.5 py-0.5 break-all text-[var(--foreground)]">
            {webhookUrl}
          </code>
          <button
            type="button"
            onClick={() => void copyText(webhookUrl)}
            className="font-medium text-[var(--foreground)] underline-offset-2 hover:underline"
          >
            Скопировать
          </button>
        </div>
        <p className="mt-1">Укажите этот адрес у ключа в кабинете ApiPay</p>
      </div>
      {error && <p className="mt-2 text-sm text-[var(--destructive)]">{error}</p>}
      <Button size="sm" className="mt-3 w-full" disabled={busy || (!key.trim() && !secret.trim())} onClick={submit}>
        Сохранить ключ
      </Button>
      {department.apipay_configured && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void save({ apipay_api_key: "" })}
          className="mt-2 w-full text-center text-xs font-medium text-[var(--muted-foreground)] hover:text-[var(--foreground)]"
        >
          Отключить Kaspi
        </button>
      )}
    </div>
  );
}
