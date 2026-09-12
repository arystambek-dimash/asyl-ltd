"use client";
import { Check } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import type { Department } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ALL_DEPARTMENTS } from "../scope";

/** Шторка выбора отдела — «касса» для тех, кому доступны все отделы. */
export function DepartmentSheet({
  open,
  onClose,
  departments,
  selected,
  onSelect,
}: {
  open: boolean;
  onClose: () => void;
  departments: Department[];
  selected: string;
  onSelect: (code: string) => void;
}) {
  const options = [
    { code: ALL_DEPARTMENTS, name: "Все отделы", color: null as string | null },
    ...departments.map((row) => ({ code: row.code, name: row.name, color: row.color })),
  ];
  return (
    <Modal
      variant="sheet"
      open={open}
      onClose={onClose}
      eyebrow="Касса"
      title="Отдел"
      description="Долги, оплаты и отчёт показываются по выбранному отделу."
    >
      <ul className="divide-y divide-[var(--border)] overflow-hidden rounded-xl border border-[var(--border)]">
        {options.map((option) => {
          const current = option.code === selected;
          return (
            <li key={option.code}>
              <button
                type="button"
                aria-pressed={current}
                onClick={() => {
                  onSelect(option.code);
                  onClose();
                }}
                className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors hover:bg-[var(--muted)]/60"
              >
                <span
                  aria-hidden
                  className={cn("size-2.5 shrink-0 rounded-full", !option.color && "bg-[var(--muted-foreground)]")}
                  style={option.color ? { backgroundColor: option.color } : undefined}
                />
                <span className={cn("flex-1 text-[15px]", current ? "font-semibold" : "font-medium")}>
                  {option.name}
                </span>
                {current && <Check className="size-4 shrink-0 text-[var(--primary)]" aria-hidden />}
              </button>
            </li>
          );
        })}
      </ul>
    </Modal>
  );
}
