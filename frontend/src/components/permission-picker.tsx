"use client";
import { Check } from "lucide-react";
import type { Permission } from "@/lib/types";

// Ярлыки разделов/действий каталога прав (единые для ролей и сотрудников).
export const PERM_SECTION_LABELS: Record<string, string> = {
  catalog: "Товары", clients: "Клиенты", warehouse: "Склад", orders: "Заказы",
  payments: "Оплаты", shipping: "Пост отгрузки", train: "Поезд",
  dept2: "Отдел «Сити»", events: "Журнал",
  reports: "Отчёты", employees: "Сотрудники", rbac: "Доступы",
};
export const PERM_ACTION_LABELS: Record<string, string> = {
  view: "просмотр", create: "создание", edit: "редакт.", delete: "удаление",
  adjust: "корректировка", confirm: "подтвержд.",
  view_all: "все данные", arrive: "приём", load: "загрузка",
  ship: "отгрузка", debt_override: "в долг", manage: "управление",
  set_price: "закрепление прайса",
};

/** Кнопки-переключатели прав, сгруппированные по разделам.
 * inherited — права, которые даёт роль: показаны включёнными, но не снимаются здесь. */
export function PermissionPicker({ perms, selected, onToggle, inherited }: {
  perms: Permission[];
  selected: Set<string>;
  onToggle: (code: string) => void;
  inherited?: Set<string>;
}) {
  const sections = Array.from(new Set(perms.map((p) => p.section)));
  return (
    <div className="flex flex-col gap-3">
      {sections.map((sec) => (
        <div key={sec} className="rounded-lg border p-3">
          <div className="mb-2 text-sm font-semibold">{PERM_SECTION_LABELS[sec] ?? sec}</div>
          <div className="flex flex-wrap gap-2">
            {perms.filter((p) => p.section === sec).map((p) => {
              const fromRole = inherited?.has(p.code) ?? false;
              if (fromRole) {
                return (
                  <span key={p.code}
                    title="Это право даёт роль. Изменить его можно в разделе «Роли»."
                    className="cursor-default rounded-md border border-[var(--primary)]/40 bg-[var(--primary)]/10 px-3 py-1.5 text-xs font-medium text-[var(--primary)]/80">
                    {PERM_ACTION_LABELS[p.action] ?? p.action} · роль
                  </span>
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
