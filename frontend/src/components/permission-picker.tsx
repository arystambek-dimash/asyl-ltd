"use client";
import { Check } from "lucide-react";
import type { Permission } from "@/lib/types";

const PERM_SECTION_LABELS: Record<string, string> = {
  catalog: "Товары",
  clients: "Клиенты",
  warehouse: "Склад",
  silos: "Силосы",
  tasks: "Задачи",
  grain: "Приход и проход",
  orders: "Заказы",
  payments: "Оплаты",
  shipping: "Пост отгрузки",
  train: "Вагон",
  events: "Журнал",
  reports: "Отчёты",
  employees: "Сотрудники",
  sys_permissions: "Системные права",
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
  const sections = Array.from(new Set(perms.map((permission) => permission.section)));
  return (
    <div className="flex flex-col gap-3">
      {sections.map((section) => (
        <div key={section} className="rounded-lg border p-3">
          <div className="mb-2 text-sm font-semibold">{PERM_SECTION_LABELS[section] ?? section}</div>
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
