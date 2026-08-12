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
    <div className="flex items-center gap-2">
      <LiveScaleStatus active={canWeigh} />
      <ActionMenu
        items={items}
        label="Операции с зерном"
        triggerText="Операции"
        triggerIcon={Plus}
        className="h-9 bg-[var(--primary)] px-4 text-sm text-[var(--primary-foreground)] hover:bg-[var(--primary)]/90 hover:text-[var(--primary-foreground)]"
      />
    </div>
  );
}
