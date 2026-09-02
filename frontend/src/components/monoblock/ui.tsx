"use client";

// Тихий UI-kit модалки «Робот Кука» в духе Linear/Vercel: светло, много
// воздуха, hairline-границы, крупные цифры, цвет только на данных.
// Один язык для всех вкладок — не плодим стили по месту.

import type { ReactNode } from "react";
import { useId, useState } from "react";
import { Info } from "lucide-react";

import { cn } from "@/lib/utils";

/** Карточка-лист: белый фон, почти невидимая граница, мягкая тень. */
export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "rounded-2xl border border-slate-200/70 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.04)]",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** Волосяной разделитель вместо вложенных карточек-коробок. */
export function Hairline({ className }: { className?: string }) {
  return <div className={cn("h-px bg-slate-100", className)} />;
}

/** Мелкая приглушённая надпись-метка над значением. */
export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400", className)}>
      {children}
    </div>
  );
}

/** Заголовок секции + опциональная подсказка-иконка (текст-шум прячем сюда). */
export function SectionHead({
  title,
  hint,
  aside,
  className,
}: {
  title: string;
  hint?: string;
  aside?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <h3 className="text-[15px] font-semibold tracking-tight text-slate-900">{title}</h3>
      {hint && <InfoHint text={hint} />}
      {aside && <div className="ml-auto flex items-center gap-2">{aside}</div>}
    </div>
  );
}

/** Иконка (i) с ховер-подсказкой — сюда уезжают длинные пояснения. */
export function InfoHint({ text, className }: { text: string; className?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className={cn("relative inline-flex", className)}>
      <button
        type="button"
        aria-label="Подробнее"
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className="flex size-4 items-center justify-center rounded-full text-slate-300 transition hover:text-slate-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300"
      >
        <Info className="size-3.5" />
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute left-1/2 top-6 z-20 w-56 -translate-x-1/2 rounded-lg bg-slate-900 px-3 py-2 text-[11px] font-medium leading-relaxed text-white shadow-lg"
        >
          {text}
        </span>
      )}
    </span>
  );
}

/** Метрика: крупное число + мелкая метка. Единый «дорогой» контраст размеров. */
export function Metric({
  label,
  value,
  unit,
  size = "md",
  accent = "slate",
  className,
}: {
  label?: ReactNode;
  value: ReactNode;
  unit?: string;
  size?: "sm" | "md" | "lg" | "xl";
  accent?: "slate" | "blue" | "amber";
  className?: string;
}) {
  const valueSize = {
    sm: "text-xl",
    md: "text-3xl",
    lg: "text-4xl",
    xl: "text-5xl sm:text-6xl",
  }[size];
  const accentColor = {
    slate: "text-slate-900",
    blue: "text-blue-600",
    amber: "text-amber-600",
  }[accent];
  return (
    <div className={cn("min-w-0", className)}>
      {label && <Eyebrow className="mb-1">{label}</Eyebrow>}
      <div className="flex items-baseline gap-1.5">
        <span className={cn("font-black tabular-nums tracking-tight", valueSize, accentColor)}>{value}</span>
        {unit && <span className="text-xs font-medium text-slate-400">{unit}</span>}
      </div>
    </div>
  );
}

/** Цветная точка данных (Красный/Синий/Зелёный). */
export function ColorDot({ className, pulse }: { className?: string; pulse?: boolean }) {
  return <span className={cn("size-2.5 shrink-0 rounded-full", className, pulse && "animate-pulse")} />;
}

/** Тихий статус: точка + короткая метка (вместо прогресс-баров и абзацев). */
export function StatusChip({ tone, children }: { tone: "ok" | "warn" | "error" | "muted"; children: ReactNode }) {
  const map = {
    ok: "text-emerald-600",
    warn: "text-amber-600",
    error: "text-red-600",
    muted: "text-slate-400",
  };
  const dot = {
    ok: "bg-emerald-500",
    warn: "bg-amber-500",
    error: "bg-red-500",
    muted: "bg-slate-300",
  };
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-xs font-semibold", map[tone])}>
      <span className={cn("size-1.5 rounded-full", dot[tone])} />
      {children}
    </span>
  );
}
