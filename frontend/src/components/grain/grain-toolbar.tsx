"use client";

import { Plus, TrainFront, Truck } from "lucide-react";
import { LiveScaleStatus } from "@/components/grain/live-scale-status";
import { ActionMenu, type ActionMenuItem } from "@/components/ui/action-menu";
import { Button } from "@/components/ui/button";

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
    items.push({ key: "arrival", label: "Принять поезд", icon: TrainFront, onSelect: onArrival });
  }
  if (canSupply) {
    items.push({ key: "supply", label: "Новый приход", icon: Plus, onSelect: onSupply });
  }

  if (!canWeigh && !canArrive && items.length === 0) return null;

  return (
    <div className="flex min-w-0 items-center gap-2">
      {canWeigh && (
        <div className="flex shrink-0 items-center gap-1.5" role="group" aria-label="Текущий вес">
          <LiveScaleStatus active scaleKey="wagon" label="Вагоны" />
          <LiveScaleStatus active scaleKey="truck" label="Вывоз" />
        </div>
      )}
      {canArrive && (
        <Button
          size="sm"
          aria-label="Оформить вывоз"
          title="Оформить вывоз"
          className="size-9 shrink-0 p-0 2xl:h-9 2xl:w-auto 2xl:px-3"
          onClick={onPassage}
        >
          <Truck className="size-4" /> <span className="hidden 2xl:inline">Оформить вывоз</span>
        </Button>
      )}
      {items.length > 0 && (
        <ActionMenu
          items={items}
          label="Операции прихода"
          triggerText="Приход"
          triggerIcon={Plus}
          className="h-9 shrink-0 border bg-[var(--background)] px-2 text-sm text-[var(--foreground)] shadow-xs hover:bg-[var(--accent)] [&>span]:hidden [&>svg:last-child]:hidden 2xl:px-3 2xl:[&>span]:inline 2xl:[&>svg:last-child]:block"
        />
      )}
    </div>
  );
}
