"use client";
import { Check } from "lucide-react";
import { sectionRank } from "@/lib/permission-presets";
import type { Permission } from "@/lib/types";

const PERM_SECTION_LABELS: Record<string, string> = {
  reports: "Отчёты",
  orders: "Заказы",
  payments: "Касса",
  monoblock: "Моноблок",
  loader: "Грузчик",
  warehouse: "Склады",
  silos: "Силосы",
  grain: "Приход и вывоз",
  clients: "Клиенты",
  catalog: "Товары",
  tasks: "Задачи",
  events: "Журнал",
  employees: "Сотрудники",
  sys_permissions: "Администрирование",
};

export function PermissionPicker({
  perms,
  selected,
  onToggle,
  disabled = new Set(),
}: {
  perms: Permission[];
  selected: Set<string>;
  onToggle: (code: string) => void;
  disabled?: Set<string>;
}) {
  // Разделы — в порядке меню, а не по алфавиту кодов с сервера.
  const sections = Array.from(new Set(perms.map((permission) => permission.section))).sort(
    (a, b) => sectionRank(a) - sectionRank(b),
  );
  return (
    <div className="flex flex-col gap-3">
      {sections.map((section) => (
        <div key={section} className="rounded-lg border p-3">
          <h3 className="mb-2 text-sm font-semibold">{PERM_SECTION_LABELS[section] ?? section}</h3>
          <div className="flex flex-wrap gap-2">
            {perms
              .filter((permission) => permission.section === section)
              .map((permission) => {
                const enabled = selected.has(permission.code);
                const cannotGrant = disabled.has(permission.code);
                const separator = permission.label.indexOf(":");
                const label = separator >= 0 ? permission.label.slice(separator + 1).trim() : permission.label;
                return (
                  <button
                    key={permission.code}
                    type="button"
                    disabled={cannotGrant}
                    onClick={() => onToggle(permission.code)}
                    title={cannotGrant ? "Вы не можете выдать право, которого нет у вас" : permission.label}
                    className={`inline-flex items-center gap-1 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors ${
                      enabled
                        ? "border-[var(--primary)] bg-[var(--primary)]/10 text-[var(--primary)]"
                        : "text-[var(--muted-foreground)] hover:bg-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-45"
                    }`}
                  >
                    {enabled && <Check className="size-3" />}
                    {label}
                  </button>
                );
              })}
          </div>
        </div>
      ))}
    </div>
  );
}
