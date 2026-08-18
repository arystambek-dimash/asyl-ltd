"use client";

// Источник: Aceternity UI · Bento Grid — адаптировано под монитор AI 24/7.
// `BentoGrid` задаёт адаптивную сетку, `BentoCard` — плитку с мягкой рамкой
// и опциональной подсветкой курсора (GlowingEffect).

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";
import { GlowingEffect } from "@/components/ui/aceternity/glowing-effect";

interface BentoGridProps {
  children: ReactNode;
  className?: string;
}

export function BentoGrid({ children, className }: BentoGridProps) {
  return (
    <div className={cn("grid grid-cols-1 gap-3 sm:gap-4 lg:grid-cols-3 lg:auto-rows-[minmax(0,auto)]", className)}>
      {children}
    </div>
  );
}

interface BentoCardProps {
  children: ReactNode;
  className?: string;
  /** Растяжение по колонкам на десктопе (lg). */
  colSpan?: 1 | 2 | 3;
  /** Растяжение по строкам на десктопе (lg). */
  rowSpan?: 1 | 2;
  /** Включить рамку-подсветку, следящую за курсором. */
  glow?: boolean;
  /** Сделать плитку интерактивной (даёт hover-подъём и курсор). */
  interactive?: boolean;
}

const COL_SPAN: Record<NonNullable<BentoCardProps["colSpan"]>, string> = {
  1: "lg:col-span-1",
  2: "lg:col-span-2",
  3: "lg:col-span-3",
};

const ROW_SPAN: Record<NonNullable<BentoCardProps["rowSpan"]>, string> = {
  1: "lg:row-span-1",
  2: "lg:row-span-2",
};

export function BentoCard({
  children,
  className,
  colSpan = 1,
  rowSpan = 1,
  glow = false,
  interactive = false,
}: BentoCardProps) {
  return (
    <div className={cn("relative min-w-0 rounded-2xl", COL_SPAN[colSpan], ROW_SPAN[rowSpan])}>
      {glow && (
        <GlowingEffect
          disabled={false}
          glow
          spread={40}
          proximity={64}
          inactiveZone={0.55}
          borderWidth={2}
          className="motion-reduce:hidden"
        />
      )}
      <div
        className={cn(
          "relative flex h-full flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white",
          interactive && "transition-shadow duration-300 hover:shadow-[0_12px_40px_rgba(15,23,42,0.10)]",
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
