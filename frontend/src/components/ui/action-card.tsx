"use client";

import type { HTMLAttributes, ReactNode } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";

type CardPrimaryAction = { href: string; label: string };

type SafeCardAttributes = Omit<
  HTMLAttributes<HTMLDivElement>,
  "children" | "onClick" | "onKeyDown" | "role" | "tabIndex"
>;

interface ActionCardProps extends SafeCardAttributes {
  children: ReactNode;
  primaryAction?: CardPrimaryAction;
}

const primaryActionClassName =
  "absolute inset-0 z-[1] rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ring)]";

/**
 * Карточка с большой зоной основного действия и отдельными вторичными controls.
 * Корневой div намеренно нельзя сделать role=link через props: ссылка
 * рендерится sibling-элементом и не содержит меню, select или tel.
 */
export function ActionCard({ children, primaryAction, className, ...props }: ActionCardProps) {
  return (
    <div className={cn("relative", className)} {...props}>
      {primaryAction && (
        <Link href={primaryAction.href} aria-label={primaryAction.label} className={primaryActionClassName}>
          <span className="sr-only">{primaryAction.label}</span>
        </Link>
      )}
      {children}
    </div>
  );
}
