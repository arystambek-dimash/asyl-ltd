"use client";
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
  adjust: "корректировка", confirm: "подтвержд.", cashier: "касса",
  view_all: "все данные", arrive: "приём", load: "загрузка",
  ship: "отгрузка", debt_override: "в долг", manage: "управление",
};

/** Кнопки-переключатели прав, сгруппированные по разделам. */
export function PermissionPicker({ perms, selected, onToggle }: {
  perms: Permission[];
  selected: Set<string>;
  onToggle: (code: string) => void;
}) {
  const sections = Array.from(new Set(perms.map((p) => p.section)));
  return (
    <div className="flex flex-col gap-3">
      {sections.map((sec) => (
        <div key={sec} className="rounded-lg border p-3">
          <div className="mb-2 text-sm font-semibold">{PERM_SECTION_LABELS[sec] ?? sec}</div>
          <div className="flex flex-wrap gap-2">
            {perms.filter((p) => p.section === sec).map((p) => (
              <button key={p.code} type="button" onClick={() => onToggle(p.code)}
                className={`rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                  selected.has(p.code)
                    ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                    : "text-[var(--muted-foreground)] hover:bg-[var(--accent)]"}`}>
                {PERM_ACTION_LABELS[p.action] ?? p.action}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
