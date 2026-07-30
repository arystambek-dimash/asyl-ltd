"use client";
import * as React from "react";
import { cn } from "@/lib/utils";

/** Карточка-сводка в стиле кассовых отчётов: крупное значение + расшифровка. */
export function SummaryCard({
  title,
  value,
  valueTitle,
  tone = "plain",
  extra,
  rows,
}: {
  title: string;
  value: string;
  /** Точное значение в подсказке, когда value показан компактно («4,93 млрд ₸»). */
  valueTitle?: string;
  tone?: "success" | "destructive" | "primary" | "plain";
  /** Дополнительное содержимое между значением и строками (например, бар долей). */
  extra?: React.ReactNode;
  rows: { label: string; value: string; strong?: boolean }[];
}) {
  const toneClass = {
    success: "text-[var(--success)]",
    destructive: "text-[var(--destructive)]",
    primary: "text-[var(--ring)]",
    plain: "text-[var(--foreground)]",
  }[tone];
  return (
    <div className="flex flex-col rounded-xl border border-[var(--border)] bg-[var(--card)] p-5 shadow-card">
      <span className="text-[13px] font-medium text-[var(--muted-foreground)]">{title}</span>
      <div
        title={valueTitle}
        className={cn("mt-1 text-[26px] font-bold leading-none tracking-tight tabular-nums", toneClass)}
      >
        {value}
      </div>
      {extra}
      <div className="mt-4 flex flex-col gap-2 border-t border-[var(--border)] pt-3">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-3 text-[13px]">
            <span className="text-[var(--muted-foreground)]">{r.label}</span>
            <span className={cn("tabular-nums", r.strong && "font-semibold")}>{r.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
