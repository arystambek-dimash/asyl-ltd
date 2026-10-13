"use client";
import { useState } from "react";
import { Check, Pencil, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Tabs } from "@/components/ui/tabs";
import { api, apiError } from "@/lib/api";
import { useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";
import type { Department } from "@/lib/types";
import { useAuth } from "@/store/auth";
import { ApiPayAccounts } from "./apipay-accounts";

const DEPARTMENT_COLORS = ["#315FD5", "#D68B2C", "#238C6E", "#B84A5A", "#7654B3", "#3B7F91", "#6B7280"];

type Section = "departments" | "kaspi";

/** Модалка «Отделы продаж»: название, цвет, основной/отключён; суперюзеру — ещё вкладка подключений Kaspi (ApiPay). */
export function DepartmentManager({ onChanged }: { onChanged: () => void }) {
  const { me } = useAuth();
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<Section>("departments");
  const { data, reload } = useApi<Department[]>(open ? "/departments/?all=1" : null);
  const [editing, setEditing] = useState<Department | null>(null);
  const [name, setName] = useState("");
  const [color, setColor] = useState(DEPARTMENT_COLORS[0]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const kaspiManager = Boolean(me?.is_superuser);
  const withoutKaspi = (data ?? []).filter((row) => row.is_active && !row.apipay_configured).length;

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
          setSection("departments");
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
        {kaspiManager && (
          <Tabs
            className="mb-4"
            label="Настройка отделов"
            active={section}
            onChange={(key) => setSection(key as Section)}
            tabs={[
              { key: "departments", label: "Отделы" },
              { key: "kaspi", label: "Kaspi · ApiPay", count: withoutKaspi || undefined },
            ]}
          />
        )}
        {section === "kaspi" && kaspiManager ? (
          <ApiPayAccounts
            departments={data ?? []}
            onChanged={async () => {
              await reload();
              onChanged();
            }}
          />
        ) : (
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
                      {department.order_count} заказов · {department.is_active ? "доступен" : "отключён"}
                    </div>
                    <KaspiStatus
                      department={department}
                      onOpen={kaspiManager ? () => setSection("kaspi") : undefined}
                    />
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
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}

/** Kaspi отдела в строке списка: название подключения или предупреждение; суперюзеру — переход к подключениям. */
function KaspiStatus({ department, onOpen }: { department: Department; onOpen?: () => void }) {
  const label = department.apipay_configured ? `Kaspi: ${department.apipay_account_name}` : "без Kaspi";
  const content = (
    <>
      <span
        className={cn(
          "size-1.5 rounded-full",
          department.apipay_configured ? "bg-[var(--success)]" : "bg-[var(--warning)]",
        )}
      />
      {label}
    </>
  );
  const classes = cn(
    "mt-0.5 flex w-fit items-center gap-1 text-[11px]",
    department.apipay_configured ? "text-[var(--muted-foreground)]" : "text-[var(--warning)]",
  );
  if (!onOpen) return <span className={classes}>{content}</span>;
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(classes, "underline-offset-2 hover:underline")}
      title="Настроить Kaspi"
    >
      {content}
    </button>
  );
}
