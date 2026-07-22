"use client";
import { Check, LockKeyhole, Minus } from "lucide-react";
import type { Permission } from "@/lib/types";

// Ярлыки разделов/действий каталога прав (единые для ролей и сотрудников).
export const PERM_SECTION_LABELS: Record<string, string> = {
  catalog: "Товары", clients: "Клиенты", warehouse: "Склад", orders: "Заказы",
  payments: "Оплаты", shipping: "Пост отгрузки", train: "Вагон",
  events: "Журнал",
  reports: "Отчёты", employees: "Сотрудники", rbac: "Доступы",
};
export const PERM_ACTION_LABELS: Record<string, string> = {
  view: "просмотр", create: "создание", edit: "редакт.", delete: "удаление",
  adjust: "корректировка", confirm: "подтвержд.",
  arrive: "приём", load: "загрузка",
  ship: "отгрузка", rollback: "откат отгрузки", debt_override: "в долг", manage: "управление",
  set_price: "закрепление прайса",
};

/** Кнопки-переключатели прав, сгруппированные по разделам.
 * inherited — права роли; denied позволяет точечно запретить их сотруднику. */
export function PermissionPicker({ perms, selected, onToggle, inherited, denied, onToggleDenied, forced }: {
  perms: Permission[];
  selected: Set<string>;
  onToggle: (code: string) => void;
  inherited?: Set<string>;
  denied?: Set<string>;
  onToggleDenied?: (code: string) => void;
  forced?: Set<string>;
}) {
  const sections = Array.from(new Set(perms.map((p) => p.section)));
  return (
    <div className="flex flex-col gap-3">
      {sections.map((sec) => (
        <div key={sec} className="rounded-lg border p-3">
          <div className="mb-2 text-sm font-semibold">{PERM_SECTION_LABELS[sec] ?? sec}</div>
          <div className="flex flex-wrap gap-2">
            {perms.filter((p) => p.section === sec).map((p) => {
              if (forced?.has(p.code)) {
                return (
                  <span key={p.code}
                    title="Обязательное право сотрудника отдела продаж"
                    className="inline-flex items-center gap-1 rounded-md border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">
                    <LockKeyhole className="size-3" />
                    {PERM_ACTION_LABELS[p.action] ?? p.action} · обязательно
                  </span>
                );
              }
              const fromRole = inherited?.has(p.code) ?? false;
              if (fromRole) {
                const blocked = denied?.has(p.code) ?? false;
                return (
                  <button key={p.code} type="button"
                    onClick={() => onToggleDenied?.(p.code)}
                    title={blocked ? "Личный запрет: нажмите, чтобы вернуть право роли" : "Право роли: нажмите, чтобы запретить этому сотруднику"}
                    className={`inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                      blocked
                        ? "border-red-200 bg-red-50 text-red-700 hover:bg-red-100"
                        : "border-[var(--primary)]/40 bg-[var(--primary)]/10 text-[var(--primary)] hover:bg-[var(--primary)]/15"
                    }`}>
                    {blocked ? <Minus className="size-3" /> : <Check className="size-3" />}
                    {PERM_ACTION_LABELS[p.action] ?? p.action} · {blocked ? "запрещено" : "роль"}
                  </button>
                );
              }
              const on = selected.has(p.code);
              return (
                <button key={p.code} type="button" onClick={() => onToggle(p.code)}
                  className={`inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                    on
                      ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                      : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]"}`}>
                  {on && <Check className="size-3" />}
                  {PERM_ACTION_LABELS[p.action] ?? p.action}
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
