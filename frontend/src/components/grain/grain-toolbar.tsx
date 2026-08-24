"use client";

import { Plus, TrainFront, Truck } from "lucide-react";
import { LiveScaleStatus } from "@/components/grain/live-scale-status";
import { ActionMenu, type ActionMenuItem } from "@/components/ui/action-menu";

export function GrainToolbar({
  canArrive,
  canSupply,
  canWeigh,
  onPassage,
  onArrival,
  onSupply,
}: {
  canArrive: boolean;
  canSupply: boolean;
  canWeigh: boolean;
  onPassage: () => void;
  onArrival: () => void;
  onSupply: () => void;
}) {
  const items: ActionMenuItem[] = [];
  if (canArrive) {
    items.push(
      { key: "passage", label: "Оформить вывоз", icon: Truck, onSelect: onPassage },
      { key: "arrival", label: "Принять поезд", icon: TrainFront, onSelect: onArrival },
    );
  }
  if (canSupply) {
    items.push({ key: "supply", label: "Новый приход", icon: Plus, onSelect: onSupply });
  }

  if (!canWeigh && items.length === 0) return null;

  return (
    <div className="flex min-w-0 items-center gap-2">
      {canWeigh && (
        <div className="flex shrink-0 items-center gap-1.5" role="group" aria-label="Текущий вес">
          <LiveScaleStatus active scaleKey="wagon" label="Вагоны" />
          <LiveScaleStatus active scaleKey="truck" label="Вывоз" />
        </div>
      )}
      <ActionMenu
        items={items}
        label="Операции с зерном"
        triggerText="Операции"
        triggerIcon={Plus}
        className="h-9 bg-[var(--primary)] px-2 text-sm text-[var(--primary-foreground)] hover:bg-[var(--primary)]/90 hover:text-[var(--primary-foreground)] [&>span]:hidden [&>svg:last-child]:hidden xl:px-4 xl:[&>span]:inline xl:[&>svg:last-child]:block"
      />
    </div>
  );
}
