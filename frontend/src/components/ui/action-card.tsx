"use client";

import type { HTMLAttributes, ReactNode } from "react";
import Link from "next/link";
import { cn } from "@/lib/utils";

type CardPrimaryAction =
  { kind: "link"; href: string; label: string } | { kind: "button"; label: string; onSelect: () => void };

type SafeCardAttributes = Omit<
  HTMLAttributes<HTMLDivElement>,
  "children" | "onClick" | "onKeyDown" | "role" | "tabIndex"
>;

export interface ActionCardProps extends SafeCardAttributes {
  children: ReactNode;
  primaryAction?: CardPrimaryAction;
  primaryActionClassName?: string;
}

const primaryActionClassName =
  "absolute inset-0 z-[1] rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--ring)]";

/**
 * Карточка с большой зоной основного действия и отдельными вторичными controls.
 * Корневой div намеренно нельзя сделать role=link/button через props: ссылка
 * или кнопка рендерится sibling-элементом и не содержит меню, select или tel.
 */
export function ActionCard({
  children,
  primaryAction,
  primaryActionClassName: actionClassName,
  className,
  ...props
}: ActionCardProps) {
  return (
    <div className={cn("relative", className)} {...props}>
      {primaryAction?.kind === "link" && (
        <Link
          href={primaryAction.href}
          aria-label={primaryAction.label}
          data-card-primary-action
          className={cn(primaryActionClassName, actionClassName)}
        >
          <span className="sr-only">{primaryAction.label}</span>
        </Link>
      )}
      {primaryAction?.kind === "button" && (
        <button
          type="button"
          aria-label={primaryAction.label}
          data-card-primary-action
          onClick={primaryAction.onSelect}
          className={cn(primaryActionClassName, actionClassName)}
        />
      )}
      {children}
    </div>
  );
}
