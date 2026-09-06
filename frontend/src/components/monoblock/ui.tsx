"use client";

// Тихий UI-kit модалки «Робот Кука» на токенах дизайн-системы (Card, border,
// muted): без градиентов и анимаций, hairline-разделители, крупные табличные
// цифры, цвет только на данных. Один язык для всех вкладок — не плодим
// стили по месту.

import type { ReactNode } from "react";
import { useId, useState } from "react";
import { Info } from "lucide-react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** Карточка на токенах Card; testid — якорь для тестов вместо класса скругления. */
export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Card data-testid="always-on-panel" className={cn("rounded-lg", className)}>
      {children}
    </Card>
  );
}

/** Волосяной разделитель вместо вложенных карточек-коробок. */
export function Hairline({ className }: { className?: string }) {
  return <div className={cn("h-px bg-[var(--border)]", className)} />;
}

/** Мелкая приглушённая подпись над значением — как подпись StatCard. */
export function Eyebrow({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("text-[12px] font-medium text-[var(--muted-foreground)]", className)}>{children}</div>;
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
      <h3 className="text-[15px] font-semibold tracking-tight">{title}</h3>
      {hint && <InfoHint text={hint} />}
      {aside && <div className="ml-auto flex items-center gap-2">{aside}</div>}
    </div>
  );
}

/** Иконка (i) с подсказкой по наведению и фокусу — сюда уезжают длинные пояснения. */
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
        className="flex size-4 items-center justify-center rounded-full text-[var(--muted-foreground)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--ring)]/40"
      >
        <Info className="size-3.5" />
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute left-1/2 top-6 z-20 w-56 -translate-x-1/2 rounded-md border border-[var(--border)] bg-[var(--card)] px-3 py-2 text-[12px] leading-relaxed text-[var(--card-foreground)] shadow-card"
        >
          {text}
        </span>
      )}
    </span>
  );
}

/** Метрика: табличное число + мелкая подпись, те же размеры, что у StatCard. */
export function Metric({
  label,
  value,
  unit,
  size = "md",
  className,
}: {
  label?: ReactNode;
  value: ReactNode;
  unit?: string;
  size?: "sm" | "md";
  className?: string;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      {label && <Eyebrow className="mb-1">{label}</Eyebrow>}
      <div className="flex items-baseline gap-1.5">
        <span
          className={cn(
            "font-semibold leading-[1.1] tracking-tight tabular-nums text-[var(--foreground)]",
            size === "sm" ? "text-[20px]" : "text-[30px]",
          )}
        >
          {value}
        </span>
        {unit && <span className="text-[12px] text-[var(--muted-foreground)]">{unit}</span>}
      </div>
    </div>
  );
}

/** Цветная точка данных (Красный/Синий/Зелёный). */
export function ColorDot({ className }: { className?: string }) {
  return <span className={cn("size-2.5 shrink-0 rounded-full", className)} />;
}

/** Тихий статус: точка + короткая метка (вместо прогресс-баров и абзацев). */
export function StatusChip({ tone, children }: { tone: "ok" | "warn" | "error" | "muted"; children: ReactNode }) {
  const map = {
    ok: "text-[var(--success)]",
    warn: "text-[var(--warning)]",
    error: "text-[var(--destructive)]",
    muted: "text-[var(--muted-foreground)]",
  };
  const dot = {
    ok: "bg-[var(--success)]",
    warn: "bg-[var(--warning)]",
    error: "bg-[var(--destructive)]",
    muted: "bg-[var(--muted-foreground)]",
  };
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[12px] font-medium", map[tone])}>
      <span className={cn("size-1.5 rounded-full", dot[tone])} />
      {children}
    </span>
  );
}
