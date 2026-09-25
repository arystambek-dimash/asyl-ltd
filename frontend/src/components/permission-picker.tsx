"use client";
import { Check } from "lucide-react";
import type { Permission } from "@/lib/types";

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
  // Сервер отдаёт права в порядке меню: разделы идут в порядке первого появления.
  const sections = Array.from(new Map(perms.map((permission) => [permission.section, permission.section_label])));
  return (
    <div className="flex flex-col gap-3">
      {sections.map(([section, sectionLabel]) => (
        <div key={section} className="rounded-lg border p-3">
          <h3 className="mb-2 text-sm font-semibold">{sectionLabel}</h3>
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
