"use client";

import { Plus, TrainFront, Truck } from "lucide-react";
import { LiveScaleStatus } from "@/components/grain/live-scale-status";
import { ActionMenu, type ActionMenuItem } from "@/components/ui/action-menu";
import { Button } from "@/components/ui/button";

export function GrainToolbar({
  direction,
  canArrive,
  canSupply,
  canWeigh,
  onPassage,
  onArrival,
  onSupply,
}: {
  direction: "intake" | "passage";
  canArrive: boolean;
  canSupply: boolean;
  canWeigh: boolean;
  onPassage: () => void;
  onArrival: () => void;
  onSupply: () => void;
}) {
  const items: ActionMenuItem[] = [];
  if (direction === "intake" && canArrive) {
    items.push({ key: "arrival", label: "Принять поезд", icon: TrainFront, onSelect: onArrival });
  }
  if (direction === "intake" && canSupply) {
    items.push({ key: "supply", label: "Новый приход", icon: Plus, onSelect: onSupply });
  }

  if (direction === "intake" && items.length === 0) return null;
  if (direction === "passage" && !canWeigh && !canArrive) return null;

  return (
    <div className="flex min-w-0 items-center gap-2">
      {direction === "passage" && canWeigh && (
        <div className="flex shrink-0 items-center" role="group" aria-label="Текущий вес вывоза">
          <LiveScaleStatus active scaleKey="truck" label="Вывоз" />
        </div>
      )}
      {direction === "passage" && canArrive && (
        <Button size="sm" title="Оформить вывоз" className="h-9 shrink-0 px-3" onClick={onPassage}>
          <Truck className="size-4" /> Оформить вывоз
        </Button>
      )}
      {direction === "intake" && items.length > 0 && (
        <ActionMenu
          items={items}
          label="Операции прихода"
          triggerText="Приход"
          triggerIcon={Plus}
          className="h-9 shrink-0 border bg-[var(--background)] px-3 text-sm text-[var(--foreground)] shadow-xs hover:bg-[var(--accent)]"
        />
      )}
    </div>
  );
}
